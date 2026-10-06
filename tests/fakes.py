from langchain_core.messages import AIMessage


class FakeLLM:
    """Scripted stand-in for ChatAnthropic: returns canned replies in order and records every call."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def invoke(self, messages, *args, **kwargs):
        self.calls.append(messages)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return AIMessage(content=reply)


def sql_block(sql: str) -> str:
    return f"```sql\n{sql}\n```"
