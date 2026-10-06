using System.Text.Json;

internal static class Program
{
    private const int Protocol = 1;

    public static async Task Main()
    {
        string? line;
        while ((line = await Console.In.ReadLineAsync()) is not null)
        {
            if (string.IsNullOrWhiteSpace(line)) continue;
            JsonDocument? doc = null;
            try
            {
                doc = JsonDocument.Parse(line);
                var root = doc.RootElement;
                var requestId = root.GetProperty("request_id").GetString() ?? "";
                var action = root.GetProperty("action").GetString() ?? "";
                var payload = root.GetProperty("payload");
                var response = action switch
                {
                    "health" => Ok(requestId, action, new { status = "ready" }),
                    "decide" => DecidePlaceholder(requestId, action, payload),
                    _ => Error(requestId, action, "unknown action"),
                };
                Console.WriteLine(JsonSerializer.Serialize(response));
                await Console.Out.FlushAsync();
            }
            catch (Exception ex)
            {
                Console.WriteLine(JsonSerializer.Serialize(new { protocol = Protocol, request_id = "", ok = false, action = "", payload = new { }, error = ex.Message }));
                await Console.Out.FlushAsync();
            }
            finally { doc?.Dispose(); }
        }
    }

    private static object DecidePlaceholder(string requestId, string action, JsonElement payload)
    {
        // Stage 1 intentionally passes. Stage 2 wires the real Game.Shared.AI runtime.
        return Ok(requestId, action, new { kind = "pass", payload = new { reason = "legacy-ai-runtime-not-wired" } });
    }

    private static object Ok(string requestId, string action, object payload) => new { protocol = Protocol, request_id = requestId, ok = true, action, payload, error = (string?)null };
    private static object Error(string requestId, string action, string error) => new { protocol = Protocol, request_id = requestId, ok = false, action, payload = new { }, error };
}
