using System.Collections;
using System.Reflection;
using System.Reflection.Emit;
using System.Runtime.CompilerServices;
using System.Runtime.Loader;
using System.Text.Json;

namespace LegacyAiWorker;

public sealed record EventEnvelope(int ClassId, string DataBase64, long Sequence = 0);

public sealed class CaptureSink
{
    private readonly List<object> _transactions = new();
    public IReadOnlyList<object> Transactions => _transactions;
    public bool Capture(object transaction)
    {
        _transactions.Add(transaction);
        return true;
    }
    public void Clear() => _transactions.Clear();
}

public sealed class HeadlessAiMirror : IDisposable
{
    private readonly Assembly _assembly;
    private readonly Type _sessionEventType;
    private readonly MethodInfo _buildArgs;
    private readonly MethodInfo _routeMessage;
    private readonly object _mirror;
    private readonly CaptureSink _sink;

    private HeadlessAiMirror(
        Assembly assembly, Type sessionEventType,
        MethodInfo buildArgs, MethodInfo routeMessage,
        object mirror, CaptureSink sink)
    {
        _assembly = assembly;
        _sessionEventType = sessionEventType;
        _buildArgs = buildArgs;
        _routeMessage = routeMessage;
        _mirror = mirror;
        _sink = sink;
    }

    public static HeadlessAiMirror Create(
        Assembly assembly,
        ulong sessionUid64,
        ulong aiUid64,
        ulong humanUid64,
        int aiPosition,
        string sessionName)
    {
        string stage = "start";
        try
        {
            return CreateCore(
                assembly, sessionUid64, aiUid64, humanUid64,
                aiPosition, sessionName);
        }
        catch (Exception ex)
        {
            throw new InvalidOperationException(
                $"Headless AI mirror construction failed at '{stage}': {ex.Message}", ex);
        }
    }

    private static HeadlessAiMirror CreateCore(
        Assembly assembly,
        ulong sessionUid64,
        ulong aiUid64,
        ulong humanUid64,
        int aiPosition,
        string sessionName)
    {
        string stage = "type lookup";
        try
        {
            static Type Required(Assembly asm, string fullName) =>
                asm.GetType(fullName, true, false)
                ?? throw new InvalidOperationException($"Missing client type: {fullName}");

        stage = "type lookup";
        var uidType = Required(assembly, "Game.Shared.UID");
        var sessionStateType = Required(assembly, "Game.Shared.SessionState");
        var sessionEventType = Required(assembly, "Game.Shared.SessionEventArgs");
        var clientSessionBaseType = Required(assembly, "Game.Shared.ClientSessionBase");
        var transactionType = Required(
            assembly, "Game.Shared.Mechanics.Transactions.Transaction");
        var playerStateType = Required(assembly, "Game.Shared.PlayerState");
        var aiPlayerType = Required(assembly, "Game.Shared.AIPlayer");
        var remotePlayerType = Required(assembly, "Game.Shared.RemotePlayer");
        var personalityType = Required(assembly, "Game.Shared.AI.AIPersonality");
        var playerType = Required(assembly, "Game.Shared.Player");

        var buildArgs = sessionEventType.GetMethod(
            "BuildArgs",
            BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static,
            binder: null, types: new[] { typeof(int), typeof(byte[]) }, modifiers: null)
            ?? throw new InvalidOperationException("SessionEventArgs.BuildArgs is unavailable");

        var routeMessage = clientSessionBaseType.GetMethod(
            "RouteMessage",
            BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance,
            binder: null, types: new[] { sessionEventType }, modifiers: null)
            ?? throw new InvalidOperationException("ClientSessionBase.RouteMessage is unavailable");

        var addPlayer = clientSessionBaseType.GetMethods(
                BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance)
            .FirstOrDefault(m =>
            {
                if (m.Name != "AddPlayer") return false;
                var p = m.GetParameters();
                return p.Length == 1 && p[0].ParameterType.IsAssignableFrom(playerType);
            })
            ?? throw new InvalidOperationException("ClientSessionBase.AddPlayer(Player) is unavailable");

        var initializeAi = aiPlayerType.GetMethod(
            "InitializeAITactical",
            BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance,
            binder: null, types: new[] { personalityType }, modifiers: null)
            ?? throw new InvalidOperationException("AIPlayer.InitializeAITactical is unavailable");

        stage = "construct SessionState";
        var state = Activator.CreateInstance(sessionStateType)
            ?? throw new InvalidOperationException("Cannot create SessionState");
        SetMember(state, "SessionId", MakeUid(uidType, sessionUid64));
        SetMember(state, "SessionName", sessionName);
        SetMember(state, "MinimumPlayerCount", 2);
        SetMember(state, "MaximumPlayerCount", 2);

        stage = "construct AI PlayerState";
        var aiState = Activator.CreateInstance(playerStateType)
            ?? throw new InvalidOperationException("Cannot create AI PlayerState");
        SetMember(aiState, "PlayerId", MakeUid(uidType, aiUid64));
        SetMember(aiState, "PlayerPosition", aiPosition);

        stage = "construct human PlayerState";
        var humanState = Activator.CreateInstance(playerStateType)
            ?? throw new InvalidOperationException("Cannot create human PlayerState");
        SetMember(humanState, "PlayerId", MakeUid(uidType, humanUid64));
        SetMember(humanState, "PlayerPosition", aiPosition == 0 ? 1 : 0);

        stage = "construct AIPlayer";
        var ai = CreateSingle(aiPlayerType, aiState);
        stage = "construct RemotePlayer";
        var remote = CreateDouble(
            remotePlayerType, humanState, InvalidUid(uidType));

        stage = "create capture sink";
        var sink = new CaptureSink();
        stage = "build ClientSessionBase mirror type";
        var mirrorType = BuildMirrorType(
            clientSessionBaseType, sessionStateType, transactionType,
            sessionEventType, playerType);

        var ctor = mirrorType.GetConstructor(
            new[] { sessionStateType, typeof(CaptureSink) })
            ?? throw new InvalidOperationException("generated mirror constructor missing");

        stage = "construct ClientSessionBase mirror";
        var mirror = ctor.Invoke(new object[] { state, sink });
        stage = "add AI player to mirror";
        addPlayer.Invoke(mirror, new[] { ai });
        stage = "add remote player to mirror";
        addPlayer.Invoke(mirror, new[] { remote });

        stage = "construct AIPersonality";
        var personality = CreateDefault(personalityType);
        stage = "InitializeAITactical";
        initializeAi.Invoke(ai, new[] { personality });

        stage = "create HeadlessAiMirror wrapper";
        return new HeadlessAiMirror(
            assembly, sessionEventType, buildArgs, routeMessage, mirror, sink);
        }
        catch (Exception ex)
        {
            throw new InvalidOperationException(
                $"Headless AI mirror construction failed at '{stage}': {ex.Message}", ex);
        }
    }

    public IReadOnlyList<object> Transactions => _sink.Transactions;

    public void ClearTransactions() => _sink.Clear();

    public void Route(int classId, byte[] data)
    {
        var e = _buildArgs.Invoke(null, new object[] { classId, data });
        if (e is null)
            throw new InvalidOperationException($"BuildArgs returned null for {classId}");
        _routeMessage.Invoke(_mirror, new[] { e });
    }

    public void Dispose() => _sink.Clear();

    private static Type BuildMirrorType(
        Type baseType, Type sessionStateType, Type transactionType,
        Type sessionEventType, Type playerType)
    {
        var name = new AssemblyName("HexOriginalAiMirror." + Guid.NewGuid().ToString("N"));
        var asm = AssemblyBuilder.DefineDynamicAssembly(name, AssemblyBuilderAccess.Run);
        var module = asm.DefineDynamicModule("Main");
        var type = module.DefineType(
            "HeadlessClientSession",
            TypeAttributes.Public | TypeAttributes.Class | TypeAttributes.Sealed,
            baseType);

        var sinkField = type.DefineField(
            "_sink", typeof(CaptureSink), FieldAttributes.Private);

        var baseCtor = baseType.GetConstructor(
            BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance,
            binder: null, types: new[] { sessionStateType }, modifiers: null)
            ?? throw new InvalidOperationException("ClientSessionBase(SessionState) unavailable");

        var ctor = type.DefineConstructor(
            MethodAttributes.Public, CallingConventions.Standard,
            new[] { sessionStateType, typeof(CaptureSink) });
        var ci = ctor.GetILGenerator();
        ci.Emit(OpCodes.Ldarg_0);
        ci.Emit(OpCodes.Ldarg_1);
        ci.Emit(OpCodes.Call, baseCtor);
        ci.Emit(OpCodes.Ldarg_0);
        ci.Emit(OpCodes.Ldarg_2);
        ci.Emit(OpCodes.Stfld, sinkField);
        ci.Emit(OpCodes.Ret);

        Override(type, baseType, "SubmitTransaction",
            new[] { transactionType }, typeof(bool),
            il =>
            {
                il.Emit(OpCodes.Ldarg_0);
                il.Emit(OpCodes.Ldfld, sinkField);
                il.Emit(OpCodes.Ldarg_1);
                il.Emit(OpCodes.Callvirt,
                    typeof(CaptureSink).GetMethod(nameof(CaptureSink.Capture))!);
                il.Emit(OpCodes.Ret);
            });

        Override(type, baseType, "Update", Type.EmptyTypes, typeof(bool),
            il => { il.Emit(OpCodes.Ldc_I4_0); il.Emit(OpCodes.Ret); });

        Override(type, baseType, "IsWaitingOnTransaction",
            Type.EmptyTypes, typeof(bool),
            il => { il.Emit(OpCodes.Ldc_I4_0); il.Emit(OpCodes.Ret); });

        Override(type, baseType, "UpdateThread",
            Type.EmptyTypes, typeof(void),
            il => il.Emit(OpCodes.Ret));

        Override(type, baseType, "DispatchSessionEvent",
            new[] { playerType, sessionEventType }, typeof(void),
            il => il.Emit(OpCodes.Ret));

        return type.CreateType()
            ?? throw new InvalidOperationException("Could not create mirror type");
    }

    private static void Override(
        TypeBuilder builder, Type baseType, string name, Type[] args,
        Type returnType, Action<ILGenerator> body)
    {
        var baseMethod = baseType.GetMethod(
            name, BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance,
            binder: null, types: args, modifiers: null)
            ?? throw new InvalidOperationException($"Missing ClientSessionBase.{name}");

        var attrs = MethodAttributes.Public | MethodAttributes.HideBySig |
                    MethodAttributes.Virtual | MethodAttributes.Final;
        if (baseMethod.IsFamily || baseMethod.IsFamilyOrAssembly)
            attrs = MethodAttributes.Family | MethodAttributes.HideBySig |
                    MethodAttributes.Virtual | MethodAttributes.Final;

        var method = builder.DefineMethod(
            name, attrs, CallingConventions.Standard, returnType, args);
        body(method.GetILGenerator());
        builder.DefineMethodOverride(method, baseMethod);
    }

    private static object InvalidUid(Type uidType) =>
        GetStatic(uidType, "Invalid")
        ?? MakeUid(uidType, 0);

    private static object MakeUid(Type uidType, ulong value)
    {
        var c = uidType.GetConstructor(new[] { typeof(ulong) });
        if (c is not null) return c.Invoke(new object[] { value });
        var d = uidType.GetConstructor(new[] { typeof(long) });
        if (d is not null) return d.Invoke(new object[] { unchecked((long)value) });
        throw new InvalidOperationException("UID does not expose ulong/long constructor");
    }

    private static object CreateDefault(Type type)
    {
        var ctor = type.GetConstructors(
                BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance)
            .FirstOrDefault(c => c.GetParameters().Length == 0)
            ?? throw new InvalidOperationException($"No parameterless {type.FullName} constructor");
        return ctor.Invoke(Array.Empty<object>());
    }

    private static object CreateSingle(Type type, object arg)
    {
        var c = type.GetConstructors(
                BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance)
            .FirstOrDefault(x =>
            {
                var p = x.GetParameters();
                return p.Length == 1 && p[0].ParameterType.IsInstanceOfType(arg);
            })
            ?? throw new InvalidOperationException($"No compatible {type.FullName} constructor");
        return c.Invoke(new[] { arg });
    }

    private static object CreateDouble(Type type, object a, object b)
    {
        var c = type.GetConstructors(
                BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance)
            .FirstOrDefault(x =>
            {
                var p = x.GetParameters();
                return p.Length == 2 &&
                       p[0].ParameterType.IsInstanceOfType(a) &&
                       p[1].ParameterType.IsInstanceOfType(b);
            })
            ?? throw new InvalidOperationException($"No compatible {type.FullName}(state, uid) constructor");
        return c.Invoke(new[] { a, b });
    }

    private static object? GetStatic(Type type, string name)
    {
        var f = type.GetField(name,
            BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static);
        if (f is not null) return f.GetValue(null);
        var p = type.GetProperty(name,
            BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static);
        return p?.GetValue(null);
    }

    private static void SetMember(object target, string name, object? value)
    {
        var t = target.GetType();
        var p = t.GetProperty(name,
            BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance);
        if (p is not null && p.CanWrite)
        {
            p.SetValue(target, ConvertValue(value, p.PropertyType));
            return;
        }
        var f = t.GetField(name,
            BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance);
        if (f is not null)
        {
            f.SetValue(target, ConvertValue(value, f.FieldType));
            return;
        }
        throw new InvalidOperationException($"Cannot set {t.FullName}.{name}");
    }

    private static object? ConvertValue(object? value, Type type)
    {
        if (value is null) return null;
        if (type.IsInstanceOfType(value)) return value;
        if (type.IsEnum) return Enum.ToObject(type, value);
        return Convert.ChangeType(value, type);
    }
}

public sealed class OriginalAiRuntime : IDisposable
{
    private sealed class ClientLoadContext : AssemblyLoadContext
    {
        private readonly string _directory;

        public ClientLoadContext(string mainAssemblyPath)
            : base("HexOriginalClient." + Guid.NewGuid().ToString("N"), isCollectible: false)
        {
            _directory = Path.GetDirectoryName(Path.GetFullPath(mainAssemblyPath))
                ?? throw new InvalidOperationException("Could not determine client DLL directory");
        }

        protected override Assembly? Load(AssemblyName assemblyName)
        {
            if (string.IsNullOrWhiteSpace(assemblyName.Name))
                return null;

            var candidate = Path.Combine(_directory, assemblyName.Name + ".dll");
            return File.Exists(candidate)
                ? LoadFromAssemblyPath(candidate)
                : null;
        }
    }

    private sealed class SessionMirror : IDisposable
    {
        public HeadlessAiMirror Mirror { get; }
        public HashSet<string> AppliedEvents { get; } = new(StringComparer.Ordinal);

        public SessionMirror(
            Assembly assembly,
            ulong sessionUid64,
            ulong aiUid64,
            ulong humanUid64,
            int aiPosition,
            string sessionName)
        {
            Mirror = HeadlessAiMirror.Create(
                assembly, sessionUid64, aiUid64, humanUid64,
                aiPosition, sessionName);
        }

        public void Dispose() => Mirror.Dispose();
    }

    private readonly Assembly _assembly;
    private readonly ClientLoadContext _loadContext;
    private readonly string _clientDll;
    private readonly Dictionary<string, SessionMirror> _sessions = new(StringComparer.Ordinal);
    private readonly object _gate = new();

    public OriginalAiRuntime(string clientDll)
    {
        if (string.IsNullOrWhiteSpace(clientDll))
            throw new ArgumentException("HEX_CLIENT_DLL is empty", nameof(clientDll));
        if (!File.Exists(clientDll))
            throw new FileNotFoundException("HEX client DLL not found", clientDll);

        _clientDll = Path.GetFullPath(clientDll);
        _loadContext = new ClientLoadContext(_clientDll);
        _assembly = _loadContext.LoadFromAssemblyPath(_clientDll);
    }

    public object Health()
    {
        var ai = _assembly.GetType("Game.Shared.AIPlayer", throwOnError: false) is not null;
        var tactical = _assembly.GetType("Game.Shared.AI.AITactical", throwOnError: false) is not null;
        var session = _assembly.GetType("Game.Shared.ClientSessionBase", throwOnError: false) is not null;
        var events = _assembly.GetType("Game.Shared.SessionEventArgs", throwOnError: false) is not null;
        return new
        {
            status = ai && tactical && session && events ? "ready" : "blocked",
            ai_types = ai && tactical,
            client_session = session,
            session_events = events,
            active_sessions = _sessions.Count,
            assembly = _assembly.FullName,
            client_dll = _clientDll,
            client_directory = Path.GetDirectoryName(_clientDll)
        };
    }

    public object Probe(
        ulong sessionUid64, ulong aiUid64, ulong humanUid64,
        int aiPosition, string sessionName)
    {
        lock (_gate)
        {
            var key = $"{sessionUid64}:{aiUid64}";
            if (!_sessions.ContainsKey(key))
            {
                _sessions[key] = new SessionMirror(
                    _assembly, sessionUid64, aiUid64, humanUid64,
                    aiPosition, sessionName);
            }

            return new
            {
                status = "ready",
                session_uid64 = sessionUid64,
                ai_uid64 = aiUid64,
                human_uid64 = humanUid64,
                ai_position = aiPosition,
                active_sessions = _sessions.Count
            };
        }
    }

    public Dictionary<string, object?> Decide(
        ulong sessionUid64, ulong aiUid64, ulong humanUid64,
        int aiPosition, string sessionName, IEnumerable<EventEnvelope> events)
    {
        lock (_gate)
        {
            var key = $"{sessionUid64}:{aiUid64}";
            if (!_sessions.TryGetValue(key, out var session))
            {
                session = new SessionMirror(
                    _assembly, sessionUid64, aiUid64, humanUid64,
                    aiPosition, sessionName);
                _sessions[key] = session;
            }

            foreach (var item in events.OrderBy(e => e.Sequence))
            {
                var marker = $"{item.Sequence}:{item.ClassId}:{item.DataBase64}";
                if (!session.AppliedEvents.Add(marker))
                    continue;

                var bytes = Convert.FromBase64String(item.DataBase64);
                session.Mirror.Route(item.ClassId, bytes);
            }

            var tx = session.Mirror.Transactions.LastOrDefault();
            session.Mirror.ClearTransactions();
            if (tx is null)
            {
                throw new InvalidOperationException(
                    "Original Game.Shared.AI produced no transaction");
            }

            return TransactionProjector.Project(tx);
        }
    }

    public void ResetSession(ulong sessionUid64, ulong aiUid64)
    {
        lock (_gate)
        {
            var key = $"{sessionUid64}:{aiUid64}";
            if (_sessions.Remove(key, out var session))
                session.Dispose();
        }
    }

    public void Dispose()
    {
        lock (_gate)
        {
            foreach (var session in _sessions.Values)
                session.Dispose();
            _sessions.Clear();
            // The client load context is intentionally process-lifetime and non-collectible.
            // Do not call Unload() on a non-collectible AssemblyLoadContext.
        }
    }
}

public static class TransactionProjector
{
    private static ulong? Uid64(object? value)
    {
        if (value is null) return null;
        if (value is ulong u) return u;
        if (value is long l) return unchecked((ulong)l);
        if (value is int i) return unchecked((ulong)i);

        var type = value.GetType();
        foreach (var name in new[] { "uid64", "m_UID64", "UID64", "value", "Value" })
        {
            var p = type.GetProperty(name,
                BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance);
            if (p is not null)
            {
                var nested = p.GetValue(value);
                var result = Uid64(nested);
                if (result.HasValue) return result;
            }
            var f = type.GetField(name,
                BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance);
            if (f is not null)
            {
                var nested = f.GetValue(value);
                var result = Uid64(nested);
                if (result.HasValue) return result;
            }
        }
        return null;
    }

    public static Dictionary<string, object?> Project(object transaction)
    {
        var name = transaction.GetType().Name;
        return name switch
        {
            "PassPriorityTransaction" => Decision("pass", new()),
            "ChoosePlayFirstTransaction" => Decision("choose_play_first", new()),
            "ChooseDrawFirstTransaction" => Decision("choose_draw_first", new()),
            "AcceptStartingHandTransaction" => Decision("accept_starting_hand", new()),
            "MulliganTransaction" => Decision("mulligan", new()),
            "RequestPrioritySyncTransaction" => Decision("request_priority_sync", new()),
            "CancelAutoPassTransaction" => Decision("cancel_auto_pass", new()),
            "SetAutoPassTransaction" => Decision("set_auto_pass", new()
            {
                ["as_active"] = BoolValue(transaction, false, "m_AsActive", "AsActive"),
                ["passing_state"] = IntValue(transaction, "m_PassingState", "PassingState")
            }),
            "PlayChampionTransaction" => Decision("play_champion",
                new() { ["card_id"] = UidRequired(transaction, "m_SessionCardId", "SessionCardId") }),
            "PlayResourceTransaction" => Decision("play_resource",
                new() { ["card_id"] = UidRequired(transaction, "m_SessionCardId", "SessionCardId") }),
            "PlayTroopTransaction" => CardDecision("play_troop", transaction),
            "PlayArtifactTransaction" => CardDecision("play_artifact", transaction),
            "PlaySpellTransaction" => CardDecision("play_spell", transaction),
            "DiscardTransaction" => Decision("discard",
                new() { ["card_id"] = UidRequired(transaction, "m_SessionCardId", "SessionCardId") }),
            "ActivateTriggeredAbiliesTransaction" or "ActivateTriggeredAbilitiesTransaction" =>
                Decision("activate_triggered_abilities", new()
                {
                    ["activation_data"] = FindStructured(
                        transaction, "m_AbilityActivationData",
                        "AbilityActivationData") ?? Array.Empty<object>()
                }),
            "SetAbilityActivationDataTransaction" => Decision("set_ability_activation_data", new()
                {
                    ["ability_instance_id"] = IntValue(
                        transaction, "AbilityInstanceId", "m_AbilityInstanceId"),
                    ["activation_data"] = FindStructured(
                        transaction, "m_AbilityActivationData",
                        "AbilityActivationData") ?? new Dictionary<string, object?>()
                }),
                        "ActivateAbilityTransaction" => Decision("activate_ability", new()
            {
                ["source_card_id"] = UidRequired(transaction, "SourceCardId", "m_SourceCardId",
                    "m_SessionCardId", "SessionCardId"),
                ["ability_template_id"] = GuidRequired(transaction,
                    "AbilityTemplateId", "m_AbilityTemplateId"),
                ["ability_instance_id"] = IntValue(transaction,
                    "AbilityInstanceId", "m_AbilityInstanceId"),
                ["activation_data"] = FindStructured(transaction,
                    "m_AbilityActivationData", "AbilityActivationData")
                    ?? new Dictionary<string, object?>()
            }),
            "CommitTroopsToAttackTransaction" => Decision(
                "commit_troops_to_attack",
                new() { ["declarations"] = ProjectDeclarations(transaction) }),
            "CommitTroopsToDefenseTransaction" => Decision(
                "commit_troops_to_defense",
                new() { ["declarations"] = ProjectDeclarations(transaction) }),
            _ => throw new NotSupportedException(
                $"Unsupported original AI transaction: {name}")
        };
    }

    private static Dictionary<string, object?> CardDecision(string kind, object tx) =>
        Decision(kind, new()
        {
            ["card_id"] = UidRequired(tx, "m_SessionCardId", "SessionCardId", "m_CardId"),
            ["ability_data"] = FindStructured(tx, "m_AbilityDataList", "AbilityDataList")
                ?? Array.Empty<object>(),
            ["playing_for_free"] = BoolValue(tx, false, "PlayingForFree", "m_PlayingForFree")
        });

    private static Dictionary<string, object?> Decision(
        string kind, Dictionary<string, object?> payload) =>
        new() { ["kind"] = kind, ["payload"] = payload };

    private static ulong UidRequired(object root, params string[] names)
    {
        foreach (var name in names)
        {
            var value = FindMember(root, name);
            var uid = Uid64(value);
            if (uid.HasValue) return uid.Value;
        }
        throw new InvalidOperationException(
            $"No UID field in {root.GetType().Name}: {string.Join(",", names)}");
    }

    private static string GuidRequired(object root, params string[] names)
    {
        foreach (var name in names)
        {
            var value = FindMember(root, name);
            if (value is Guid g) return g.ToString();
            if (value is string s && s.Length > 0) return s;
            var nested = FindMember(value, "m_Guid", "Guid", "guid");
            if (nested is Guid ng) return ng.ToString();
            if (nested is string ns && ns.Length > 0) return ns;
        }
        throw new InvalidOperationException(
            $"No GUID field in {root.GetType().Name}: {string.Join(",", names)}");
    }

    private static long IntValue(object root, params string[] names)
    {
        foreach (var name in names)
        {
            var value = FindMember(root, name);
            if (value is int i) return i;
            if (value is long l) return l;
            if (value is uint u) return u;
            if (value is ulong ul) return checked((long)ul);
            if (long.TryParse(value?.ToString(), out var x)) return x;
        }
        return 0;
    }

    private static bool BoolValue(object root, bool fallback, params string[] names)
    {
        foreach (var name in names)
        {
            var value = FindMember(root, name);
            if (value is bool b) return b;
            if (value is int i) return i != 0;
        }
        return fallback;
    }

    private static object? FindStructured(object root, params string[] names)
    {
        foreach (var name in names)
        {
            var value = FindMember(root, name);
            if (value is not null) return Normalize(value);
        }
        return null;
    }

    private static List<Dictionary<string, object?>> ProjectDeclarations(object root)
    {
        var raw = FindMember(root, "m_Attacks", "Attacks", "m_DefenseDeclarations", "DefenseDeclarations",
            "m_Defenses", "Defenses", "m_BlockingAssignments", "BlockingAssignments");
        if (raw is not IEnumerable list)
            return new();

        var result = new List<Dictionary<string, object?>>();
        foreach (var item in list)
        {
            if (item is null) continue;
            var defender = FindMember(item, "DefendingCardId", "m_DefendingCardId",
                "AttackerId", "m_AttackerId", "Defender", "m_Defender");
            var attackers = FindMember(item, "AttackingCardIds", "m_AttackingCardIds",
                "DefendingCardIds", "m_DefendingCardIds", "BlockingCardIds", "m_BlockingCardIds");
            var defenderId = Uid64(defender);
            if (!defenderId.HasValue) continue;

            var ids = new List<ulong>();
            if (attackers is IEnumerable attackList)
            {
                foreach (var x in attackList)
                {
                    var id = Uid64(x);
                    if (id.HasValue) ids.Add(id.Value);
                }
            }
            result.Add(new()
            {
                ["defender"] = defenderId.Value,
                ["attackers"] = ids
            });
        }
        return result;
    }

    private static object? FindMember(object? root, params string[] names)
    {
        if (root is null) return null;
        var visited = new HashSet<object>(ReferenceEqualityComparer.Instance);
        return FindMember(root, names, visited, 0);
    }

    private static object? FindMember(
        object root, string[] names, HashSet<object> visited, int depth)
    {
        if (root is null || depth > 5) return null;
        if (!root.GetType().IsValueType && !visited.Add(root)) return null;

        var t = root.GetType();
        foreach (var name in names)
        {
            var p = t.GetProperty(name,
                BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance);
            if (p is not null) return p.GetValue(root);
            var f = t.GetField(name,
                BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance);
            if (f is not null) return f.GetValue(root);
        }

        foreach (var f in t.GetFields(
            BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance))
        {
            if (f.IsStatic || f.FieldType.IsPrimitive || f.FieldType.IsEnum ||
                f.FieldType == typeof(string) || f.FieldType == typeof(Guid))
                continue;
            object? v;
            try { v = f.GetValue(root); } catch { continue; }
            if (v is null) continue;
            var found = FindMember(v, names, visited, depth + 1);
            if (found is not null) return found;
        }

        return null;
    }

    private static object? Normalize(object? value, int depth = 0)
    {
        if (value is null || depth > 5) return null;
        if (value is string || value is bool || value is byte || value is sbyte ||
            value is short || value is ushort || value is int || value is uint ||
            value is long || value is ulong || value is float || value is double ||
            value is decimal)
            return value;
        if (value is Guid g) return g.ToString();
        if (Uid64(value) is { } uid) return uid;
        if (value is IEnumerable e && value is not string)
        {
            var list = new List<object?>();
            foreach (var item in e) list.Add(Normalize(item, depth + 1));
            return list;
        }
        var dict = new Dictionary<string, object?>();
        foreach (var f in value.GetType().GetFields(
            BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance))
        {
            if (f.IsStatic) continue;
            object? v;
            try { v = f.GetValue(value); } catch { continue; }
            dict[f.Name] = Normalize(v, depth + 1);
        }
        return dict;
    }

    private sealed class ReferenceEqualityComparer : IEqualityComparer<object>
    {
        public static readonly ReferenceEqualityComparer Instance = new();
        public new bool Equals(object? x, object? y) => ReferenceEquals(x, y);
        public int GetHashCode(object obj) => RuntimeHelpers.GetHashCode(obj);
    }
}
