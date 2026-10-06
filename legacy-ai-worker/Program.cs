using System.Reflection;
using System.Runtime.Loader;
using System.Text.Json;

internal static class Program
{
    private const int Protocol = 1;
    private static Assembly? _clientAssembly;
    private static object? _plugin;
    private static MethodInfo? _decideMethod;

    public static async Task Main()
    {
        InitializeRuntime();
        string? line;
        while ((line = await Console.In.ReadLineAsync()) is not null)
        {
            if (string.IsNullOrWhiteSpace(line)) continue;
            try
            {
                using var doc = JsonDocument.Parse(line);
                var root = doc.RootElement;
                var requestId = root.GetProperty("request_id").GetString() ?? "";
                var action = root.GetProperty("action").GetString() ?? "";
                object response = action switch
                {
                    "health" => Success(requestId, action, Health()),
                    "decide" => Success(requestId, action, Decide(root.GetProperty("payload"))),
                    _ => Error(requestId, action, "unknown action"),
                };
                Console.WriteLine(JsonSerializer.Serialize(response));
                await Console.Out.FlushAsync();
            }
            catch (Exception ex)
            {
                Console.WriteLine(JsonSerializer.Serialize(new
                {
                    protocol = Protocol, request_id = "", ok = false, action = "",
                    payload = new { }, error = ex.Message
                }));
                await Console.Out.FlushAsync();
            }
        }
    }

    private static void InitializeRuntime()
    {
        var clientPath = Environment.GetEnvironmentVariable("HEX_CLIENT_DLL");
        if (!string.IsNullOrWhiteSpace(clientPath) && File.Exists(clientPath))
        {
            _clientAssembly = AssemblyLoadContext.Default.LoadFromAssemblyPath(
                Path.GetFullPath(clientPath));
        }

        var pluginPath = Environment.GetEnvironmentVariable("HEX_ORIGINAL_AI_PLUGIN");
        if (!string.IsNullOrWhiteSpace(pluginPath) && File.Exists(pluginPath))
        {
            var pluginAssembly = AssemblyLoadContext.Default.LoadFromAssemblyPath(
                Path.GetFullPath(pluginPath));
            var entryName = Environment.GetEnvironmentVariable("HEX_ORIGINAL_AI_ENTRY");
            var type = !string.IsNullOrWhiteSpace(entryName)
                ? pluginAssembly.GetType(entryName!, false)
                : pluginAssembly.GetTypes().FirstOrDefault(t =>
                    t.GetMethod("Decide", BindingFlags.Public | BindingFlags.Static | BindingFlags.Instance,
                        binder: null, types: new[] { typeof(string) }, modifiers: null) is not null);
            if (type is null)
                throw new InvalidOperationException(
                    "HEX_ORIGINAL_AI_PLUGIN has no public Decide(string) entry point");

            _decideMethod = type.GetMethod(
                "Decide", BindingFlags.Public | BindingFlags.Static | BindingFlags.Instance,
                binder: null, types: new[] { typeof(string) }, modifiers: null);
            if (_decideMethod is null)
                throw new InvalidOperationException("Original AI Decide(string) method not found");
            if (!_decideMethod.IsStatic)
                _plugin = Activator.CreateInstance(type);
        }
    }

    private static object Health()
    {
        var aiPlayer = _clientAssembly?.GetType("Game.Shared.AI.AIPlayer") is not null;
        var tactical = _clientAssembly?.GetType("Game.Shared.AI.AITactical") is not null;
        var plugin = _decideMethod is not null;
        return new
        {
            status = plugin && aiPlayer && tactical ? "ready" : "blocked",
            client_ai_types = aiPlayer && tactical,
            plugin,
            client_assembly = _clientAssembly is not null,
            required = new[] {
                "Game.Shared.AI.AIPlayer",
                "Game.Shared.AI.AITactical",
                "HEX_ORIGINAL_AI_PLUGIN"
            }
        };
    }

    private static object Decide(JsonElement payload)
    {
        if (_decideMethod is null)
            throw new InvalidOperationException(
                "Original AI runtime is not configured. Set HEX_CLIENT_DLL and HEX_ORIGINAL_AI_PLUGIN. Python AI fallback remains available.");

        var request = payload.GetRawText();
        var result = _decideMethod.IsStatic
            ? _decideMethod.Invoke(null, new object[] { request })
            : _decideMethod.Invoke(_plugin, new object[] { request });

        if (result is null)
            throw new InvalidOperationException("Original AI returned null");

        var json = result switch
        {
            string text => text,
            JsonDocument document => document.RootElement.GetRawText(),
            _ => JsonSerializer.Serialize(result),
        };
        using var doc = JsonDocument.Parse(json);
        if (doc.RootElement.ValueKind != JsonValueKind.Object)
            throw new InvalidOperationException("Original AI response is not an object");
        return JsonSerializer.Deserialize<Dictionary<string, object?>>(json)
               ?? throw new InvalidOperationException("Original AI response could not be decoded");
    }

    private static object Success(string requestId, string action, object payload) =>
        new { protocol = Protocol, request_id = requestId, ok = true, action, payload, error = (string?)null };

    private static object Error(string requestId, string action, string error) =>
        new { protocol = Protocol, request_id = requestId, ok = false, action, payload = new { }, error };
}
