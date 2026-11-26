"""A scripted fake chat model, so the agent graph runs in tests without an API key."""
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import RunnableLambda
from pydantic import Field


class ScriptedLLM(BaseChatModel):
    """A fake chat model. Replies come from `script` in order (an AIMessage, or a
    function of the messages that returns one). Records every call in `calls`."""
    script: list = Field(default_factory=list)
    calls: list = Field(default_factory=list)
    route: str = "data"

    @property
    def _llm_type(self):
        return "scripted"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.calls.append(messages)
        reply = self.script.pop(0) if self.script else AIMessage("Done.")
        if callable(reply):
            reply = reply(messages)
        reply.usage_metadata = reply.usage_metadata or {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120}
        return ChatResult(generations=[ChatGeneration(message=reply)])

    def bind_tools(self, tools, **kwargs):
        return self

    def with_structured_output(self, schema, **kwargs):
        def pick(_messages):
            self.calls.append(_messages)
            return schema(route=self.route)
        return RunnableLambda(pick)


def tool_call(name, **args):
    return AIMessage("", tool_calls=[{"name": name, "args": args, "id": f"call_{name}_{len(str(args))}"}])
