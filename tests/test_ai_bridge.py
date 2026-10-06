import unittest
from integration.ai_bridge.protocol import AiRequest, decode_request, encode_request

class AiBridgeTests(unittest.TestCase):
    def test_jsonl_request_roundtrip(self):
        req = AiRequest('r1', 'decide', {'player_id': 7})
        decoded = decode_request(encode_request(req))
        self.assertEqual(decoded.request_id, 'r1')
        self.assertEqual(decoded.action, 'decide')
        self.assertEqual(decoded.payload['player_id'], 7)

if __name__ == '__main__':
    unittest.main()
