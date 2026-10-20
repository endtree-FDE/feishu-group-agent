# 模型接口。cursor、openai_compat、anthropic、fake 都从这里进、从这里出。
# 输入是已经拼好的 prompt、会话键、进度回调；输出是最终文字，或 PartialText（时间到了，只有半截）。
class PartialText(str):
    """卡死或超过防卡死上限时，已经输出的那部分文字。可能是空字符串。"""


def emit(on_stage, stage=None, think=None, end=False, text=None):
    """进度回调，和原来的进度卡片同一形状。
    stage：一句话阶段（正在思考、正在读《标题》、正在查资料）。
    think：思考片段；end 为真表示这段思考结束。
    text：回答的文字片段。旧回调如果不认识 text，就忽略，不影响作答。
    """
    if not on_stage:
        return
    try:
        if text is not None:
            on_stage(None, text=text)
        elif think is not None:
            on_stage(None, think=think, end=end)
        elif stage:
            on_stage(stage)
    except TypeError:
        try:
            if think is not None:
                on_stage(None, think=think, end=end)
            elif stage:
                on_stage(stage)
        except Exception:
            pass
    except Exception:
        pass


class Provider:
    name = "base"

    def complete(self, prompt, sess=None, on_stage=None):
        """返回最终文字、PartialText，或 None（这次没有答出来）。"""
        raise NotImplementedError

    def drop(self, sess):
        """丢掉某个会话。没有会话可丢的实现什么都不做。"""

    def close(self):
        pass

    def check_items(self):
        """--check 用。返回 [(名字, 是否通过, 说明), ...]。说明里不能出现密钥。"""
        return []
