"""解析器自测（不依赖真实 boss 进程）。"""

from __future__ import annotations

from tools.collect_history import parse_chat_messages, parse_list_names


def test_parse_list_names() -> None:
    raw = """沟通列表共 2 人，其中 1 人有未读消息。
候选人明细：
1. 张三｜前端工程师｜未读:1｜时间:刚刚｜消息:你好
2. 李四｜后端｜时间:昨天｜消息:在吗
"""
    assert parse_list_names(raw) == ["张三", "李四"]


def test_parse_chat_messages() -> None:
    raw = """成功进入候选人聊天：张三
简历获取状态: 未获取

人才摘要：

姓名: 张三

完整聊天消息：

[candidate] 10:30 你好，这个岗位还在招吗？
[you] 10:31 在的，方便简单介绍一下近况吗
[system] 对方已查看
[candidate] 昨天 15:20 我目前在职，周内可以面试
[you] 昨天 15:22 好，那我们约周三下午？
"""
    msgs = parse_chat_messages(raw)
    assert msgs[0] == {"role": "candidate", "text": "你好，这个岗位还在招吗？", "time": "10:30"}
    assert msgs[1]["role"] == "recruiter"
    assert msgs[2]["role"] == "system"
    assert msgs[3]["text"] == "我目前在职，周内可以面试"
    assert msgs[3]["time"] == "昨天 15:20"


if __name__ == "__main__":
    test_parse_list_names()
    test_parse_chat_messages()
    print("ok")
