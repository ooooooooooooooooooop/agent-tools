import unittest
import subprocess
import json
from pathlib import Path

class TestToolCallIdNormalization(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.node_script = Path(__file__).parent / "_test_tool_call_id_norm.mjs"
        cls.node_script.write_text("""
import { convertResponsesMessages } from 'file:///C:/Users/admin/.dsh/profiles/web/base-dsh-0.1.1-rc.2/node_modules/@deepseek-ai/dsh/node_modules/@earendil-works/pi-ai/dist/api/openai-responses-shared.js';
import { shortHash } from 'file:///C:/Users/admin/.dsh/profiles/web/base-dsh-0.1.1-rc.2/node_modules/@deepseek-ai/dsh/node_modules/@earendil-works/pi-ai/dist/utils/hash.js';

function runTest(tc) {
    const model = { provider: 'cpa', api: 'openai-responses', id: 'gemini-3.7-flash-high', compat: {}, input: ['text'] };
    const context = {
        messages: [
            {
                role: 'assistant',
                provider: 'cpa',
                api: 'openai-responses',
                model: 'gemini-3.7-flash-high',
                content: tc.toolCalls.map(t => ({
                    type: 'toolCall',
                    id: t.id,
                    name: t.name,
                    arguments: t.arguments || {}
                }))
            },
            ...tc.toolResults.map(r => ({
                role: 'toolResult',
                toolCallId: r.toolCallId,
                toolName: r.toolName,
                content: [{ type: 'text', text: r.content || '{}' }]
            }))
        ]
    };
    const allowed = new Set(['cpa']);
    const res = convertResponsesMessages(model, context, allowed, {});
    return res;
}

const action = process.argv[2];
const payload = JSON.parse(process.argv[3] || '{}');

if (action === 'test_case') {
    const res = runTest(payload);
    console.log(JSON.stringify(res));
}
""", encoding='utf-8')

    @classmethod
    def tearDownClass(cls):
        if cls.node_script.is_file():
            cls.node_script.unlink()

    def _run_node_case(self, tool_calls, tool_results):
        payload = json.dumps({"toolCalls": tool_calls, "toolResults": tool_results})
        proc = subprocess.run(
            ["node", str(self.node_script), "test_case", payload],
            capture_output=True,
            text=True,
            check=True
        )
        return json.loads(proc.stdout)

    def test_01_short_call_id_preserved(self):
        res = self._run_node_case(
            [{"id": "call_short_123", "name": "test_tool"}],
            [{"toolCallId": "call_short_123", "toolName": "test_tool"}]
        )
        self.assertEqual(res[0]["call_id"], "call_short_123")
        self.assertEqual(res[1]["call_id"], "call_short_123")
        self.assertLessEqual(len(res[0]["call_id"]), 64)

    def test_02_exact_64_char_id(self):
        id64 = "a" * 64
        res = self._run_node_case(
            [{"id": id64, "name": "test_tool"}],
            [{"toolCallId": id64, "toolName": "test_tool"}]
        )
        self.assertEqual(res[0]["call_id"], id64)
        self.assertEqual(res[1]["call_id"], id64)
        self.assertEqual(len(res[0]["call_id"]), 64)

    def test_03_65_char_id_normalized_under_64(self):
        id65 = "a" * 65
        res = self._run_node_case(
            [{"id": id65, "name": "test_tool"}],
            [{"toolCallId": id65, "toolName": "test_tool"}]
        )
        self.assertLessEqual(len(res[0]["call_id"]), 64)
        self.assertLessEqual(len(res[1]["call_id"]), 64)
        self.assertEqual(res[0]["call_id"], res[1]["call_id"])
        self.assertTrue(res[0]["call_id"].startswith("a"))

    def test_04_real_78_char_fixture(self):
        id78 = "mcp__agent-switchboard__list_managed_claude_supervisors-1788342742715561900-89"
        res = self._run_node_case(
            [{"id": id78, "name": "mcp__agent-switchboard__list_managed_claude_supervisors"}],
            [{"toolCallId": id78, "toolName": "mcp__agent-switchboard__list_managed_claude_supervisors"}]
        )
        self.assertLessEqual(len(res[0]["call_id"]), 64)
        self.assertLessEqual(len(res[1]["call_id"]), 64)
        self.assertEqual(res[0]["call_id"], res[1]["call_id"])
        self.assertEqual(res[0]["call_id"], "mcp__agent-switchboard__list_managed_claude_supervisors_11kk1zxf")

    def test_05_200_plus_char_id(self):
        id200 = "very_long_custom_tool_call_id_prefix_" * 8
        res = self._run_node_case(
            [{"id": id200, "name": "test_tool"}],
            [{"toolCallId": id200, "toolName": "test_tool"}]
        )
        self.assertLessEqual(len(res[0]["call_id"]), 64)
        self.assertLessEqual(len(res[1]["call_id"]), 64)
        self.assertEqual(res[0]["call_id"], res[1]["call_id"])

    def test_06_call_result_bilateral_consistency(self):
        ids = [
            "short_1",
            "mcp__agent-switchboard__request_context_snapshot-1788342768002850300-90",
            "mcp__agent-switchboard__get_latest_context_snapshot-1788342771566973900-91"
        ]
        tool_calls = [{"id": x, "name": "tool_" + str(i)} for i, x in enumerate(ids)]
        tool_results = [{"toolCallId": x, "toolName": "tool_" + str(i)} for i, x in enumerate(ids)]
        res = self._run_node_case(tool_calls, tool_results)
        
        # 3 calls then 3 results
        call_ids = [r["call_id"] for r in res if r["type"] == "function_call"]
        result_ids = [r["call_id"] for r in res if r["type"] == "function_call_output"]
        self.assertEqual(call_ids, result_ids)
        for cid in call_ids:
            self.assertLessEqual(len(cid), 64)

    def test_07_no_collision_on_identical_prefix(self):
        idA = "mcp__agent-switchboard__list_managed_claude_supervisors-1788342742715561900-89"
        idB = "mcp__agent-switchboard__list_managed_claude_supervisors-1788342742715561900-90"
        res = self._run_node_case(
            [{"id": idA, "name": "toolA"}, {"id": idB, "name": "toolB"}],
            [{"toolCallId": idA, "toolName": "toolA"}, {"toolCallId": idB, "toolName": "toolB"}]
        )
        call_ids = [r["call_id"] for r in res if r["type"] == "function_call"]
        self.assertEqual(len(call_ids), 2)
        self.assertNotEqual(call_ids[0], call_ids[1])
        self.assertLessEqual(len(call_ids[0]), 64)
        self.assertLessEqual(len(call_ids[1]), 64)

    def test_08_replay_historical_pipe_call_id(self):
        pipe_id = "mcp__agent-switchboard__list_managed_claude_supervisors-1788342742715561900-89|fc_item_123"
        res = self._run_node_case(
            [{"id": pipe_id, "name": "tool"}],
            [{"toolCallId": pipe_id, "toolName": "tool"}]
        )
        self.assertLessEqual(len(res[0]["call_id"]), 64)
        self.assertLessEqual(len(res[1]["call_id"]), 64)
        self.assertEqual(res[0]["call_id"], res[1]["call_id"])
        self.assertEqual(res[0]["id"], "fc_item_123")

    def test_09_concurrent_tool_calls_unique(self):
        ids = [f"concurrent_long_tool_call_identifier_string_{i}_{'x'*40}" for i in range(5)]
        tool_calls = [{"id": x, "name": f"tool_{i}"} for i, x in enumerate(ids)]
        tool_results = [{"toolCallId": x, "toolName": f"tool_{i}"} for i, x in enumerate(ids)]
        res = self._run_node_case(tool_calls, tool_results)
        call_ids = [r["call_id"] for r in res if r["type"] == "function_call"]
        self.assertEqual(len(call_ids), 5)
        self.assertEqual(len(set(call_ids)), 5)
        for cid in call_ids:
            self.assertLessEqual(len(cid), 64)

if __name__ == '__main__':
    unittest.main()
