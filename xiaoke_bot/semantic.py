"""Bounded semantic choices and their application to conversation behaviour."""
from __future__ import annotations

from dataclasses import replace

SCENES = {
    "neutral": "普通交流，或情境不明确",
    "explain": "认真求助、排错或讨论具体问题，需要清晰讲解或一起排查",
    "comfort": "对方低落、失落、疲惫，需要倾听和安慰",
    "joke": "善意玩笑或轻松打趣，可以适度接梗",
    "celebrate": "分享好消息、成功或喜事，适合祝贺",
    "thanks": "在感谢或肯定机器人，简短自然回应",
}
REPLY_DEPTHS = {
    "minimal": "只需一个简短反应：问候、接梗、轻微打趣、简单感谢或确认，或一句即可答清的身份/能力问题；没有待解释的具体问题",
    "normal": "需要直接回答具体信息或自然交流，按需用一到几句说清；不确定时选此项",
    "detailed": "要求讲解、原因、步骤、分析、比较、多个要点、故事或较长内容，需要完整展开",
}
TARGETS = {
    "bot": "情绪明确指向机器人本人",
    "self": "说话者自己的经历或状态",
    "person": "其他人，不是机器人",
    "thing": "服务器、软件、工作等事物或事件，不是机器人",
    "unclear": "没有明确情绪对象，或无法确定",
}
CONTINUITY = {
    "new": "新话题，或无法确定与前文关系",
    "continue": "明确接着机器人刚才的回答追问，仍需要机器人继续帮助",
    "resolved": "明确说明问题已解决或已经被别人回答，不需要再补充；即使同时道谢也选此项",
    "closing": "只有告别、结束话题或简短确认，没有明确说明问题是否解决",
    "others": "成员彼此接话，不是在和机器人继续对话",
}
MEMORY_KINDS = {
    "none": "没有适合长期记忆的本人事实",
    "durable": "新陈述自己的长期偏好、环境或稳定事实，没有提到改变或纠正过去的信息",
    "correction": "明确改变、更新或纠正过去的信息，例如现在改用 Linux、不再用 Windows；不要同时选 durable",
    "temporary": "只对眼前有效的临时计划或状态，不是长期事实",
    "joke": "玩笑、假设、猜测、引用或转述，不当成本人事实",
}
TOPIC_STATES = {
    "none": "没有明确未完成事项，也没有本人确认完成",
    "open": "当前用户正在提出一个具体尚未解决的问题或计划，如服务器断连并寻求排查；不要求出现‘我’。泛泛科普提问不算",
    "resolved": "本人明确确认之前的问题已解决、任务已完成或已放弃",
}
TOOL_INTENTS = {
    "none": "普通聊天、讨论功能、引用别人的请求，或请求对象不是机器人",
    "summary": "直接要求机器人总结本群今天的聊天",
    "unresolved": "直接要求机器人整理本群今天还没解决的问题或未完成事项",
    "reminder_create": "直接要求机器人在某个时间提醒自己做某件事",
    "reminder_list": "直接要求机器人查询自己设置的提醒",
    "reminder_cancel": "直接要求机器人取消自己某个编号的提醒",
    "memory_query": "直接询问机器人记得哪些关于自己的偏好或事实",
}
KNOWLEDGE_ACTIONS = {
    "none": "普通讨论、猜测、未验证建议，或只有解决了但没有任何可复用的具体办法",
    "confirm": "当前用户明确确认自己实际用过的具体解决办法有效，近期消息中能找到办法或本条已说明；适合整理为群内可复用方案",
    "outdated": "当前用户明确指出群里以前确认过的某个办法已失效或不再适用于新版本，不是一般疑问或猜测",
}
FEEDBACK_KINDS = {
    "none": "普通对话、引用、玩笑或没有要求修改机器人的记忆/回复偏好",
    "forget": "要求撤回关于自己的一条长期记忆，例如刚才是开玩笑别记住、不要再记我喜欢某物",
    "voice": "明确设置或取消今后的语音/文字回复偏好，例如以后代码问题用文字回答、恢复默认回复方式",
}


def behavior_enabled(settings) -> bool:
    return any((settings.jev_scene_enabled, settings.jev_continuity_enabled,
                settings.jev_memory_enabled, settings.jev_followup_enabled, settings.jev_tools_enabled,
                settings.jev_knowledge_enabled, settings.jev_feedback_enabled, settings.routine_enabled, settings.search_enabled))


def reply_depth(verdict, settings, *, needs_detail: bool = False) -> str:
    if verdict is None or not settings.jev_enabled or not settings.jev_scene_enabled:
        return "normal"
    depth = getattr(verdict, "reply_depth", "normal")
    if depth not in REPLY_DEPTHS:
        return "normal"
    if depth == "minimal" and (needs_detail or verdict.scene in {"explain", "comfort"}
            or verdict.routine_reaction == "share"
            or verdict.tool_intent != "none" or verdict.feedback_kind != "none"
            or (verdict.search_score is not None and verdict.search_score >= settings.search_threshold)):
        return "normal"
    return depth


def reply_generation_settings(depth, settings):
    return replace(settings, temperature=min(settings.temperature, 0.9)) if depth == "minimal" else settings


def scene_prompt(verdict, settings) -> str:
    if verdict is None:
        return ""
    guidance = {
        "explain": "用清晰、耐心的语气一起分析问题，先给下一步可操作的建议。",
        "comfort": "先简短接住对方的情绪，温柔倾听，不急着讲大道理。",
        "joke": "轻松接梗，梗接住就收口，不主动解释笑点或追加挑衅；不扩大攻击、不把玩笑当作事实。",
        "celebrate": "自然表达开心和祝贺，避免夸张表演。",
        "thanks": "简短亲切地回应感谢，不重复长篇解释或另起话题。",
        "neutral": "保持自然日常的表达。",
    }
    text = "本轮对话语气：" + guidance.get(verdict.scene, guidance["neutral"]) if settings.jev_scene_enabled else ""
    if settings.jev_scene_enabled and verdict.emotion_target in {"thing", "person", "self"}:
        text += " 对方的情绪不是针对你，不要表现为被冒犯或指责对方辱骂你。"
    if settings.jev_scene_enabled and settings.jev_continuity_enabled and verdict.continuity in {"resolved", "closing"}:
        text += " 话题正在结束，简短收口，不再追问。"
    if settings.routine_enabled:
        reaction = {
            "normal": "作息与这轮无关，正常回应，不主动提行程。",
            "focus": "专心回应当前问题，作息背景退后；不要用上课、作业或困倦拒绝帮助。",
            "brief": "按当前精力简短接话，不反复解释自己正在忙。",
            "share": "可以顺口聊当前活动或对方问到的今日安排，使用本轮日程，未来的活动只说计划。",
        }.get(verdict.routine_reaction, "正常回应，不主动提行程。")
        text += "\n本轮 JEV 日常回应建议：" + reaction
    return text


def semantic_mood_delta(verdict):
    if verdict is None:
        return None
    if verdict.scene in {"thanks", "celebrate"}:
        return (0.25, 0.12)
    if verdict.emotion_target == "bot" and verdict.offensive >= 0.85:
        return (-0.30, 0.22)
    return None
