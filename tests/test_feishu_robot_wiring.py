import unittest
from unittest.mock import patch


class FeishuRobotWiringTests(unittest.TestCase):
    def test_only_robot_created_selection_batches_authorize_upload(self):
        from app.agent.bitable_flow import _robot_upload_authorized

        self.assertTrue(_robot_upload_authorized([
            {"fields": {"任务批次": "sel_20260826_01"}},
        ]))
        self.assertFalse(_robot_upload_authorized([
            {"fields": {"任务批次": "manual_01"}},
        ]))
        self.assertFalse(_robot_upload_authorized([{"fields": {}}]))

    def test_only_explicit_upload_words_authorize_direct_robot_flow(self):
        from app.agent.approval import _is_upload_command

        self.assertTrue(_is_upload_command("上架PH站点3件耳环"))
        self.assertTrue(_is_upload_command("上传批次 sel_20260826"))
        self.assertFalse(_is_upload_command("采集50个耳环"))
        self.assertFalse(_is_upload_command("制作上架表"))

    def test_main_registers_message_and_card_handlers(self):
        from app import main  # noqa: F401
        from app.agent import approval
        from app.feishu import handlers

        wired = handlers.get_handlers()
        self.assertIs(wired["on_message"], approval.on_message)
        self.assertIs(wired["on_card_action"], approval.on_card_action)

    def test_registered_message_handler_forwards_feishu_instruction(self):
        from app import main  # noqa: F401
        from app.feishu import handlers

        body = {
            "header": {"event_type": "im.message.receive_v1"},
            "event": {"message": {"content": '{"text":"采集 3 个耳环到 PH"}'}},
        }
        with patch("app.agent.approval.handle_instruction", return_value={"mode": "capture_db"}) as run:
            result = handlers.handle_message(body)

        self.assertEqual(result, {"mode": "capture_db"})
        run.assert_called_once_with("采集 3 个耳环到 PH")


if __name__ == "__main__":
    unittest.main()
