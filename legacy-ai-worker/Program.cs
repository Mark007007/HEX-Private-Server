using System.Text.Json;
using LegacyAiWorker;

internal static class Program
{
    private const int Protocol = 1;
    private static OriginalAiRuntime? _runtime;
    private static string? _runtimeInitError;

    public static async Task Main()
    {
        InitializeRuntime();

        string? line;
        while ((line = await Console.In.ReadLineAsync()) is not null)
        {
            if (string.IsNullOrWhiteSpace(line))
                continue;

            JsonDocument? doc = null;
            var action = "";
            try
            {
                doc = JsonDocument.Parse(line);
                var root = doc.RootElement;
                var requestId = root.GetProperty("request_id").GetString() ?? "";
                action = root.GetProperty("action").GetString() ?? "";

                object response = action switch
                {
                    "health" => Success(
                        requestId, action,
                        _runtime is null
                            ? new
                            {
                                status = "blocked",
                                reason = _runtimeInitError ??
                                         "HEX_CLIENT_DLL is not configured"
                            }
                            : _runtime.Health()),
                    "probe" => Success(
                        requestId, action,
                        Probe(root.GetProperty("payload"))),
                    "decide" => Success(
                        requestId, action,
                        Decide(root.GetProperty("payload"))),
                    _ => Error(requestId, action, "unknown action")
                };

                Console.WriteLine(JsonSerializer.Serialize(response));
                await Console.Out.FlushAsync();
            }
            catch (Exception ex)
            {
                Console.WriteLine(JsonSerializer.Serialize(new
                {
                    protocol = Protocol,
                    request_id = "",
                    ok = false,
                    action = "",
                    payload = new { },
                    error = action == "probe"
                        ? ex.ToString()
                        : ex.GetBaseException().Message
                }));
                await Console.Out.FlushAsync();
            }
            finally
            {
                doc?.Dispose();
            }
        }
    }

    private static void InitializeRuntime()
    {
        var dll = Environment.GetEnvironmentVariable("HEX_CLIENT_DLL");
        if (string.IsNullOrWhiteSpace(dll))
            return;

        try
        {
            _runtime = new OriginalAiRuntime(dll);
            _runtimeInitError = null;
        }
        catch (Exception ex)
        {
            _runtime = null;
            _runtimeInitError = ex.GetBaseException().Message;
        }
    }

    private static object Probe(JsonElement payload)
    {
        if (_runtime is null)
            throw new InvalidOperationException(
                "Original AI runtime is unavailable; set HEX_CLIENT_DLL to Assembly-CSharp-firstpass.dll");

        var sessionUid64 = ULong(payload, "session_uid64");
        var aiUid64 = ULong(payload, "ai_player_uid64", "player_id");
        var humanUid64 = ULong(payload, "human_player_uid64", "opponent_uid64");
        var aiPosition = Int(payload, "ai_position", 1);
        var sessionName = String(payload, "session_name", "HEX AI Probe");
        var sessionFlags = ULong(payload, "session_flags");
        return _runtime.Probe(
            sessionUid64, aiUid64, humanUid64, aiPosition, sessionName,
            sessionFlags);
    }

    private static object Decide(JsonElement payload)
    {
        if (_runtime is null)
            throw new InvalidOperationException(
                "Original AI runtime is unavailable; set HEX_CLIENT_DLL to Assembly-CSharp-firstpass.dll");

        var sessionUid64 = ULong(payload, "session_uid64");
        var aiUid64 = ULong(payload, "ai_player_uid64", "player_id");
        var humanUid64 = ULong(payload, "human_player_uid64", "opponent_uid64");
        var aiPosition = Int(payload, "ai_position", 1);
        var sessionName = String(payload, "session_name", "HEX AI Session");
        var sessionFlags = ULong(payload, "session_flags");
        var events = new List<EventEnvelope>();

        if (payload.TryGetProperty("events", out var eventArray) &&
            eventArray.ValueKind == JsonValueKind.Array)
        {
            foreach (var item in eventArray.EnumerateArray())
            {
                events.Add(new EventEnvelope(
                    Int(item, "class_id", 0),
                    String(item, "data_base64", ""),
                    Long(item, "sequence", 0)));
            }
        }

        return _runtime.Decide(
            sessionUid64, aiUid64, humanUid64,
            aiPosition, sessionName, sessionFlags, events);
    }

    private static ulong ULong(JsonElement obj, params string[] names)
    {
        foreach (var name in names)
        {
            if (!obj.TryGetProperty(name, out var v))
                continue;
            if (v.ValueKind == JsonValueKind.Number && v.TryGetUInt64(out var n))
                return n;
            if (v.ValueKind == JsonValueKind.String &&
                ulong.TryParse(v.GetString(), out n))
                return n;
        }
        return 0;
    }

    private static long Long(JsonElement obj, string name, long fallback)
    {
        if (!obj.TryGetProperty(name, out var v))
            return fallback;
        if (v.ValueKind == JsonValueKind.Number && v.TryGetInt64(out var n))
            return n;
        return fallback;
    }

    private static int Int(JsonElement obj, string name, int fallback)
    {
        if (!obj.TryGetProperty(name, out var v))
            return fallback;
        if (v.ValueKind == JsonValueKind.Number && v.TryGetInt32(out var n))
            return n;
        return fallback;
    }

    private static string String(JsonElement obj, string name, string fallback)
    {
        if (!obj.TryGetProperty(name, out var v) ||
            v.ValueKind != JsonValueKind.String)
            return fallback;
        return v.GetString() ?? fallback;
    }

    private static object Success(string requestId, string action, object payload) =>
        new
        {
            protocol = Protocol,
            request_id = requestId,
            ok = true,
            action,
            payload,
            error = (string?)null
        };

    private static object Error(string requestId, string action, string error) =>
        new
        {
            protocol = Protocol,
            request_id = requestId,
            ok = false,
            action,
            payload = new { },
            error
        };
}
