const TOKEN_KEY = "xiaoke_admin_token";
const $ = (id) => document.getElementById(id);
let initialConfig = null;
let toastTimer = null;
let selectedMember = null;
let memberSearchTimer = null;
let voicePreviewUrl = null;
let routineOffset = 0;
let routineLoadSequence = 0;
let routinePlan = null;
let routineWakePending = false;
let requestOffset = 0;
let requestLoadSequence = 0;
let requestDetailSequence = 0;
let requestSearchTimer = null;
let requestKinds = {};
let requestFeatures = {};
let intelligenceData = null;
let intelligenceKind = "facts";
let intelligencePage = 0;
let intelligenceLoadSequence = 0;
const REQUEST_PAGE_SIZE = 30;
const requestStatuses = { success: "成功", error: "失败", running: "进行中", cancelled: "已取消", interrupted: "已中断" };

function setTheme(theme) {
  document.documentElement.dataset.theme = theme === "dark" ? "dark" : "light";
  const next = theme === "dark" ? "浅色主题" : "深色主题";
  $("themeToggle").textContent = next;
  $("themeToggle").setAttribute("aria-label", `切换到${next}`);
}
try { setTheme(localStorage.getItem("xiaoke_theme") || "light"); } catch { setTheme("light"); }
$("themeToggle").addEventListener("click", () => {
  const theme = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  setTheme(theme);
  try { localStorage.setItem("xiaoke_theme", theme); } catch { /* Current page still switches when storage is unavailable. */ }
});

function token() { return sessionStorage.getItem(TOKEN_KEY) || ""; }
function authHeaders() { return { "Authorization": `Bearer ${token()}`, "Content-Type": "application/json" }; }

async function api(path, options = {}) {
  const response = await fetch(path, { ...options, headers: { ...authHeaders(), ...(options.headers || {}) } });
  if (response.status === 401) { showLogin(); throw new Error("登录已失效，请重新输入管理令牌"); }
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || body.error || `请求失败 (${response.status})`);
  return body;
}

function showLogin() {
  routineLoadSequence++;
  routinePlan = null;
  $("routineWakeDialog").close();
  $("routineWakeForm").reset();
  $("routineWakeError").textContent = "";
  renderRoutineWake();
  intelligenceLoadSequence++;
  intelligenceData = null;
  $("intelligenceDetail").close();
  $("intelligenceRows").replaceChildren();
  $("intelligenceDetailMeta").replaceChildren();
  $("intelligenceDetailContent").replaceChildren();
  requestLoadSequence++;
  requestDetailSequence++;
  $("requestDetail").close();
  $("requestRows").replaceChildren();
  for (const id of ["requestInput", "requestOutput", "requestUsage", "requestDetailMeta", "requestDetailError", "requestScoreSummary"]) $(id).textContent = "";
  clearVoicePreview();
  $("routinePhotoPreview").removeAttribute("src");
  $("routinePhotoPreview").classList.add("hidden");
  $("appView").classList.add("hidden");
  $("loginView").classList.remove("hidden");
}

function showApp() {
  $("loginView").classList.add("hidden");
  $("appView").classList.remove("hidden");
}

function parseIds(value, label) {
  const parts = value.split(/[\s,，]+/).map((item) => item.trim()).filter(Boolean);
  const ids = parts.map((item) => {
    if (!/^\d+$/.test(item) || Number(item) <= 0) throw new Error(`${label}只能填写正整数`);
    return Number(item);
  });
  return [...new Set(ids)];
}

function collectConfig() {
  const superusers = parseIds($("superusers").value, "超级管理员 QQ");
  if (!superusers.length) throw new Error("至少保留一位超级管理员");
  return {
    ...collectSearchSettings(),
    search_enabled: $("searchEnabled").checked,
    search_threshold: Number($("searchThreshold").value),
    typo_enabled: $("typoEnabled").checked,
    typo_probability: Number($("typoProbability").value),
    typo_cooldown_minutes: Number($("typoCooldown").value),
    ...collectVoiceSettings(),
    voice_enabled: $("voiceEnabled").checked,
    voice_send_text: $("voiceSendText").checked,
    voice_reply_probability: Number($("voiceReplyProbability").value),
    bot_name: $("botName").value.trim(),
    routine_enabled: $("routineEnabled").checked,
    routine_sleep_silent: $("routineSleepSilent").checked,
    ...collectRoutinePhotoSettings(),
    routine_photo_enabled: $("routinePhotoEnabled").checked,
    routine_photo_trigger: $("routinePhotoTrigger").value,
    routine_photo_threshold: Number($("routinePhotoThreshold").value),
    routine_photo_cooldown_minutes: Number($("routinePhotoCooldown").value),
    routine_variation: $("routineVariation").value,
    routine_weekday_schedule: $("routineWeekday").value.trim(),
    routine_weekend_schedule: $("routineWeekend").value.trim(),
    superusers,
    allowed_groups: parseIds($("allowedGroups").value, "群号"),
    allow_superuser_private_chat: $("privateChat").checked,
    respond_without_at: $("respondWithoutAt").checked,
    probability_reply_enabled: $("probabilityReply").checked,
    reply_probability: Number($("replyProbability").value),
    trigger_keywords: $("triggerKeywords").value.split("\n").map((item) => item.trim()).filter(Boolean),
    history_messages: Number($("historyMessages").value),
    max_reply_chars: Number($("maxReplyChars").value),
    quote_reply_enabled: $("quoteReply").checked,
    quote_reply_probability: Number($("quoteReplyProbability").value),
    segment_send_enabled: $("segmentSend").checked,
    segment_probability: Number($("segmentProbability").value),
    segment_max_parts: Number($("segmentMaxParts").value),
    segment_delay_min: Number($("segmentDelayMin").value),
    segment_delay_max: Number($("segmentDelayMax").value),
    humanize_remove_punctuation: $("removePunctuation").checked,
    humanize_newline_to_space: $("newlineToSpace").checked,
    humanize_delay_enabled: $("humanizeDelay").checked,
    humanize_delay_min: Number($("delayMin").value),
    humanize_delay_max: Number($("delayMax").value),
    moderation_enabled: $("moderationEnabled").checked,
    moderation_keywords: $("moderationKeywords").value.split("\n").map((item) => item.trim()).filter(Boolean),
    moderation_exempt_admins: $("moderationExemptAdmins").checked,
    member_analysis_enabled: $("memberAnalysisEnabled").checked,
    member_analysis_auto: $("memberAnalysisAuto").checked,
    member_analysis_min_messages: Number($("memberAnalysisMinMessages").value),
    member_analysis_interval_messages: Number($("memberAnalysisIntervalMessages").value),
    member_analysis_sample_limit: Number($("memberAnalysisSampleLimit").value),
    member_message_retention: Number($("memberMessageRetention").value),
    member_profile_in_reply: $("memberProfileInReply").checked,
    mood_in_reply: $("moodInReply").checked,
    mood_half_life_hours: Number($("moodHalfLifeHours").value),
    mood_event_nudges_enabled: $("moodEventNudges").checked,
    favorability_decay_enabled: $("favorabilityDecayEnabled").checked,
    favorability_half_life_days: Number($("favorabilityHalfLifeDays").value),
    proactive_enabled: $("proactiveEnabled").checked,
    proactive_quiet_start: Number($("proactiveQuietStart").value),
    proactive_quiet_end: Number($("proactiveQuietEnd").value),
    proactive_hourly_cap: Number($("proactiveHourlyCap").value),
    proactive_daily_cap: Number($("proactiveDailyCap").value),
    proactive_cooldown_seconds: Number($("proactiveCooldownSeconds").value),
    api_base_url: $("apiBaseUrl").value.trim(),
    model: $("model").value.trim(),
    temperature: Number($("temperature").value),
    max_tokens: Number($("maxTokens").value),
    top_p: Number($("topP").value),
    presence_penalty: Number($("presencePenalty").value),
    frequency_penalty: Number($("frequencyPenalty").value),
    seed: $("seed").value.trim() === "" ? null : Number($("seed").value),
    reasoning_effort: $("reasoningEffort").value,
    response_format: $("responseFormat").value,
    stop_sequences: $("stopSequences").value.split("\n").map((item) => item.trim()).filter(Boolean),
    request_timeout: Number($("requestTimeout").value),
    extra_body_json: $("extraBodyJson").value.trim() || "{}",
    prompt_identity: $("promptIdentity").value.trim(),
    prompt_personality: $("promptPersonality").value.trim(),
    prompt_speaking_style: $("promptSpeakingStyle").value.trim(),
    prompt_group_behavior: $("promptGroupBehavior").value.trim(),
    prompt_response_preferences: $("promptResponsePreferences").value.trim(),
    prompt_boundaries: $("promptBoundaries").value.trim(),
    prompt_interests: $("promptInterests").value.trim(),
    context_timezone: $("contextTimezone").value.trim(),
    keyword_prompt_rules: collectPromptRules(),
    system_prompt: $("systemPrompt").value.trim(),
    vision_enabled: $("visionEnabled").checked,
    vision_mode: $("visionMode").value,
    vision_context_images: Number($("visionContextImages").value),
    vision_api_base_url: $("visionApiBaseUrl").value.trim(),
    vision_model: $("visionModel").value.trim(),
    vision_max_images: Number($("visionMaxImages").value),
    vision_skip_stickers: $("visionSkipStickers").checked,
    vision_prompt: $("visionPrompt").value.trim(),
    vision_timeout: Number($("visionTimeout").value),
    webhook_enabled: $("webhookEnabled").checked,
    webhook_token: $("webhookToken").value.trim(),
    webhook_target_group: Number($("webhookTargetGroup").value) || 0,
    webhook_prefix: $("webhookPrefix").value.trim(),
    webhook_template: $("webhookTemplate").value,
    fallback_enabled: $("fallbackEnabled").checked,
    fallback_api_base_url: $("fallbackApiBaseUrl").value.trim(),
    fallback_model: $("fallbackModel").value.trim(),
    offense_guard_enabled: $("offenseGuardEnabled").checked,
    offense_prompt: $("offensePrompt").value.trim(),
    offense_action: $("offenseAction").value,
    offense_mute_duration: Number($("offenseMuteDuration").value),
    offense_threshold: Number($("offenseThreshold").value),
    offense_include_admins: $("offenseIncludeAdmins").checked,
    jev_enabled: $("jevEnabled").checked,
    jev_scene_enabled: $("jevSceneEnabled").checked,
    jev_continuity_enabled: $("jevContinuityEnabled").checked,
    jev_memory_enabled: $("jevMemoryEnabled").checked,
    jev_followup_enabled: $("jevFollowupEnabled").checked,
    jev_tools_enabled: $("jevToolsEnabled").checked,
    jev_knowledge_enabled: $("jevKnowledgeEnabled").checked,
    jev_feedback_enabled: $("jevFeedbackEnabled").checked,
    jev_knowledge_max_age_days: Number($("jevKnowledgeMaxAgeDays").value),
    jev_behavior_prompt: $("jevBehaviorPrompt").value.trim(),
    jev_followup_min_hours: Number($("jevFollowupMinHours").value),
    jev_model: $("jevModel").value.trim() || "jev-latest",
    jev_timeout: Number($("jevTimeout").value),
    jev_gate_enabled: $("jevGateEnabled").checked,
    jev_offense_enabled: $("jevOffenseEnabled").checked,
    jev_addressed_threshold: Number($("jevAddressedThreshold").value),
    jev_worth_threshold: Number($("jevWorthThreshold").value),
    jev_offense_threshold: Number($("jevOffenseThreshold").value),
    jev_max_intrusion: Number($("jevMaxIntrusion").value),
    jev_min_text_length: Number($("jevMinTextLength").value),
    jev_context_messages: Number($("jevContextMessages").value),
    jev_use_proactive_budget: $("jevUseProactiveBudget").checked,
    jev_log_enabled: $("jevLogEnabled").checked,
    jev_log_retention: Number($("jevLogRetention").value),
    jev_addressed_prompt: $("jevAddressedPrompt").value.trim(),
    jev_interest_prompt: $("jevInterestPrompt").value.trim(),
    jev_interest_threshold: Number($("jevInterestThreshold").value),
    jev_worth_prompt: $("jevWorthPrompt").value.trim(),
    jev_intrusion_levels: $("jevIntrusionLevels").value.split("\n").map((line) => line.trim()).filter(Boolean),
    join_gate_enabled: $("joinGateEnabled").checked,
    join_gate_group: Number($("joinGateGroup").value) || 0,
    join_gate_api_url: $("joinGateApiUrl").value.trim(),
    join_gate_min_licenses: Number($("joinGateMinLicenses").value),
    join_gate_refresh_hours: Number($("joinGateRefreshHours").value),
    join_gate_reject_reason: $("joinGateRejectReason").value.trim(),
    summary_enabled: $("summaryEnabled").checked,
    summary_hour: Number($("summaryHour").value),
    summary_min_messages: Number($("summaryMinMessages").value),
    summary_send_enabled: $("summarySendEnabled").checked,
    api_key: $("apiKey").value.trim() || null,
    clear_api_key: $("clearApiKey").checked,
    vision_api_key: $("visionApiKey").value.trim() || null,
    clear_vision_api_key: $("clearVisionApiKey").checked,
    fallback_api_key: $("fallbackApiKey").value.trim() || null,
    clear_fallback_api_key: $("clearFallbackApiKey").checked,
    join_gate_token: $("joinGateToken").value.trim() || null,
    clear_join_gate_token: $("clearJoinGateToken").checked,
  };
}

function collectVoiceSettings() {
  return {
    voice_api_base_url: $("voiceApiBaseUrl").value.trim(),
    voice_model: $("voiceModel").value.trim(),
    voice_name: $("voiceName").value,
    voice_instructions: $("voiceInstructions").value.trim(),
    voice_pace: $("voicePace").value,
    voice_jev_enabled: $("voiceJevEnabled").checked,
    voice_jev_prompt: $("voiceJevPrompt").value.trim(),
    voice_jev_gate_enabled: $("voiceJevGateEnabled").checked,
    voice_jev_gate_prompt: $("voiceJevGatePrompt").value.trim(),
    voice_max_chars: Number($("voiceMaxChars").value),
    voice_timeout: Number($("voiceTimeout").value),
    voice_silence_seconds: Number($("voiceSilenceSeconds").value),
    voice_api_key: $("voiceApiKey").value.trim() || null,
    clear_voice_api_key: $("clearVoiceApiKey").checked,
  };
}

function collectPromptRules() {
  return [...document.querySelectorAll(".prompt-rule-card")].map((card) => ({
    name: card.querySelector(".rule-name").value.trim(),
    keywords: card.querySelector(".rule-keywords").value.split("\n").map((item) => item.trim()).filter(Boolean),
    prompt: card.querySelector(".rule-prompt").value.trim(),
    enabled: card.querySelector(".rule-enabled").checked,
    probability: Number(card.querySelector(".rule-probability").value),
  }));
}

function createPromptRule(rule = {}) {
  const fragment = $("promptRuleTemplate").content.cloneNode(true);
  const card = fragment.querySelector(".prompt-rule-card");
  card.querySelector(".rule-name").value = rule.name || "";
  card.querySelector(".rule-keywords").value = (rule.keywords || []).join("\n");
  card.querySelector(".rule-prompt").value = rule.prompt || "";
  card.querySelector(".rule-enabled").checked = rule.enabled ?? true;
  card.querySelector(".rule-probability").value = rule.probability ?? 1;
  const probabilityOutput = card.querySelector(".rule-probability-value");
  const updateProbability = () => { probabilityOutput.textContent = `${Math.round(Number(card.querySelector(".rule-probability").value) * 100)}%`; };
  card.querySelector(".rule-probability").addEventListener("input", updateProbability);
  card.querySelector(".rule-delete").addEventListener("click", () => { card.remove(); updateCounters(); markDirty(); });
  updateProbability();
  return fragment;
}

function renderPromptRules(rules) {
  $("promptRuleList").replaceChildren(...(rules || []).map(createPromptRule));
  updateCounters();
}

function fillConfig(config) {
  $("searchEnabled").checked = config.search_enabled ?? false;
  $("searchBaseUrl").value = config.search_api_base_url || "https://api.tavily.com";
  $("searchThreshold").value = config.search_threshold ?? 0.75;
  $("searchMaxResults").value = config.search_max_results ?? 5;
  $("searchTimeout").value = config.search_timeout ?? 15;
  $("searchApiKey").value = "";
  $("clearSearchKey").checked = false;
  $("searchKeyState").textContent = config.search_api_key_configured ? "已配置" : "待配置";
  $("searchState").textContent = config.search_configured ? "搜索服务已就绪，按需调用。" : config.search_api_key_configured ? "已配置 Key，搜索开关尚未开启。" : "填写搜索 Key 后保存即可使用。";
  $("typoEnabled").checked = config.typo_enabled ?? false;
  $("typoProbability").value = config.typo_probability ?? 0.04;
  $("typoCooldown").value = config.typo_cooldown_minutes ?? 30;
  $("routineEnabled").checked = config.routine_enabled ?? false;
  $("routineSleepSilent").checked = config.routine_sleep_silent ?? true;
  $("routinePhotoEnabled").checked = config.routine_photo_enabled ?? false;
  $("routinePhotoTrigger").value = config.routine_photo_trigger || "jev";
  $("routinePhotoThreshold").value = config.routine_photo_threshold ?? 0.75;
  $("routinePhotoCooldown").value = config.routine_photo_cooldown_minutes ?? 30;
  $("routinePhotoBaseUrl").value = config.routine_photo_api_base_url || "https://api.openai.com/v1";
  $("routinePhotoModel").value = config.routine_photo_model || "gpt-image-2.5-flare";
  $("routinePhotoQuality").value = config.routine_photo_quality || "medium";
  $("routinePhotoRatio").value = config.routine_photo_ratio || "auto";
  $("routinePhotoCampus").value = config.routine_photo_campus || "";
  $("routinePhotoApiKey").value = "";
  $("clearRoutinePhotoApiKey").checked = false;
  $("routinePhotoKeyState").textContent = config.routine_photo_api_key_configured ? "独立 Key 已配置" : config.routine_photo_key_available ? "复用同地址 Key" : "待配置 Key";
  $("routineVariation").value = config.routine_variation || "rich";
  $("routineWeekday").value = config.routine_weekday_schedule || "";
  $("routineWeekend").value = config.routine_weekend_schedule || "";
  initialConfig = config;
  $("voiceEnabled").checked = config.voice_enabled ?? false;
  $("jevSceneEnabled").checked = config.jev_scene_enabled ?? false;
  $("jevContinuityEnabled").checked = config.jev_continuity_enabled ?? false;
  $("jevMemoryEnabled").checked = config.jev_memory_enabled ?? false;
  $("jevFollowupEnabled").checked = config.jev_followup_enabled ?? false;
  $("jevToolsEnabled").checked = config.jev_tools_enabled ?? false;
  $("jevKnowledgeEnabled").checked = config.jev_knowledge_enabled ?? false;
  $("jevFeedbackEnabled").checked = config.jev_feedback_enabled ?? false;
  $("jevKnowledgeMaxAgeDays").value = config.jev_knowledge_max_age_days ?? 90;
  $("jevBehaviorPrompt").value = config.jev_behavior_prompt || "";
  $("jevFollowupMinHours").value = config.jev_followup_min_hours ?? 12;
  $("voiceSendText").checked = config.voice_send_text ?? true;
  $("voiceApiBaseUrl").value = config.voice_api_base_url || "https://api.openai.com/v1";
  $("voiceModel").value = config.voice_model || "gpt-live-1";
  const voiceLabels = { marin: "Marin（默认）", gleam: "Gleam（女声）", willow: "Willow（女声）", quartz: "Quartz（女声）" };
  $("voiceName").replaceChildren(...(config.voice_options || ["marin", "gleam", "willow"]).map((name) => new Option(voiceLabels[name] || name, name)));
  $("voiceName").value = config.voice_name || "marin";
  $("voiceInstructions").value = config.voice_instructions || "";
  $("voicePace").value = config.voice_pace || "natural";
  $("voiceJevEnabled").checked = config.voice_jev_enabled ?? false;
  $("voiceJevPrompt").value = config.voice_jev_prompt || "";
  $("voiceJevGateEnabled").checked = config.voice_jev_gate_enabled ?? false;
  $("voiceJevGatePrompt").value = config.voice_jev_gate_prompt || "";
  $("voiceReplyProbability").value = config.voice_reply_probability ?? 1;
  $("voiceMaxChars").value = config.voice_max_chars ?? 300;
  $("voiceTimeout").value = config.voice_timeout ?? 90;
  $("voiceSilenceSeconds").value = config.voice_silence_seconds ?? 3;
  $("voiceApiKey").value = "";
  $("clearVoiceApiKey").checked = false;
  $("voiceApiKeyState").textContent = config.voice_api_key_configured ? "已安全配置" : "未配置";
  $("botName").value = config.bot_name;
  $("superusers").value = config.superusers.join("\n");
  $("allowedGroups").value = config.allowed_groups.join("\n");
  $("privateChat").checked = config.allow_superuser_private_chat;
  $("respondWithoutAt").checked = config.respond_without_at;
  $("probabilityReply").checked = config.probability_reply_enabled;
  $("replyProbability").value = config.reply_probability;
  $("triggerKeywords").value = (config.trigger_keywords || []).join("\n");
  $("historyMessages").value = config.history_messages;
  $("maxReplyChars").value = config.max_reply_chars;
  $("quoteReply").checked = config.quote_reply_enabled ?? true;
  $("quoteReplyProbability").value = config.quote_reply_probability ?? 1;
  $("segmentSend").checked = config.segment_send_enabled;
  $("segmentProbability").value = config.segment_probability;
  $("segmentMaxParts").value = config.segment_max_parts;
  $("segmentDelayMin").value = config.segment_delay_min;
  $("segmentDelayMax").value = config.segment_delay_max;
  updateSegmentDelayPreview();
  $("removePunctuation").checked = config.humanize_remove_punctuation;
  $("newlineToSpace").checked = config.humanize_newline_to_space;
  $("humanizeDelay").checked = config.humanize_delay_enabled;
  $("delayMin").value = config.humanize_delay_min;
  $("delayMax").value = config.humanize_delay_max;
  $("moderationEnabled").checked = config.moderation_enabled;
  $("moderationKeywords").value = (config.moderation_keywords || []).join("\n");
  $("moderationExemptAdmins").checked = config.moderation_exempt_admins;
  $("memberAnalysisEnabled").checked = config.member_analysis_enabled ?? true;
  $("memberAnalysisAuto").checked = config.member_analysis_auto ?? true;
  $("memberAnalysisMinMessages").value = config.member_analysis_min_messages ?? 15;
  $("memberAnalysisIntervalMessages").value = config.member_analysis_interval_messages ?? 10;
  $("memberAnalysisSampleLimit").value = config.member_analysis_sample_limit ?? 30;
  $("memberMessageRetention").value = config.member_message_retention ?? 200;
  $("memberProfileInReply").checked = config.member_profile_in_reply ?? true;
  $("moodInReply").checked = config.mood_in_reply ?? true;
  $("moodHalfLifeHours").value = config.mood_half_life_hours ?? 6;
  $("moodEventNudges").checked = config.mood_event_nudges_enabled ?? true;
  $("favorabilityDecayEnabled").checked = config.favorability_decay_enabled ?? true;
  $("favorabilityHalfLifeDays").value = config.favorability_half_life_days ?? 30;
  $("proactiveEnabled").checked = config.proactive_enabled ?? false;
  $("proactiveQuietStart").value = config.proactive_quiet_start ?? 23;
  $("proactiveQuietEnd").value = config.proactive_quiet_end ?? 8;
  $("proactiveHourlyCap").value = config.proactive_hourly_cap ?? 2;
  $("proactiveDailyCap").value = config.proactive_daily_cap ?? 8;
  $("proactiveCooldownSeconds").value = config.proactive_cooldown_seconds ?? 1800;
  const selectedGroup = $("memberGroupFilter").value;
  $("memberGroupFilter").replaceChildren(
    Object.assign(document.createElement("option"), { value: "", textContent: "全部白名单群" }),
    ...(config.allowed_groups || []).map((groupId) => Object.assign(document.createElement("option"), { value: String(groupId), textContent: String(groupId) })),
  );
  if ([...$("memberGroupFilter").options].some((option) => option.value === selectedGroup)) $("memberGroupFilter").value = selectedGroup;
  $("apiBaseUrl").value = config.api_base_url;
  $("model").value = config.model;
  $("temperature").value = config.temperature;
  $("maxTokens").value = config.max_tokens;
  $("topP").value = config.top_p;
  $("presencePenalty").value = config.presence_penalty;
  $("frequencyPenalty").value = config.frequency_penalty;
  $("seed").value = config.seed ?? "";
  $("reasoningEffort").value = config.reasoning_effort || "";
  $("responseFormat").value = config.response_format;
  $("stopSequences").value = (config.stop_sequences || []).join("\n");
  $("requestTimeout").value = config.request_timeout;
  $("extraBodyJson").value = config.extra_body_json || "{}";
  $("promptIdentity").value = config.prompt_identity;
  $("promptPersonality").value = config.prompt_personality;
  $("promptSpeakingStyle").value = config.prompt_speaking_style;
  $("promptGroupBehavior").value = config.prompt_group_behavior;
  $("promptResponsePreferences").value = config.prompt_response_preferences;
  $("promptBoundaries").value = config.prompt_boundaries;
  $("promptInterests").value = config.prompt_interests || "";
  $("contextTimezone").value = config.context_timezone || "Asia/Taipei";
  renderPromptRules(config.keyword_prompt_rules || []);
  $("systemPrompt").value = config.system_prompt;
  $("apiKey").value = "";
  $("clearApiKey").checked = false;
  $("apiKeyState").textContent = config.api_key_configured ? "已安全配置" : "未配置";
  $("visionEnabled").checked = config.vision_enabled ?? false;
  $("visionMode").value = config.vision_mode || "separate";
  $("visionContextImages").value = config.vision_context_images ?? 6;
  updateVisionMode();
  $("visionApiBaseUrl").value = config.vision_api_base_url || "";
  $("visionModel").value = config.vision_model || "";
  $("visionMaxImages").value = config.vision_max_images ?? 3;
  $("visionSkipStickers").checked = config.vision_skip_stickers ?? true;
  $("visionPrompt").value = config.vision_prompt || "";
  $("visionTimeout").value = config.vision_timeout ?? 60;
  $("visionApiKey").value = "";
  $("clearVisionApiKey").checked = false;
  $("visionApiKeyState").textContent = config.vision_api_key_configured ? "已安全配置" : "未配置";
  $("webhookEnabled").checked = config.webhook_enabled ?? false;
  $("webhookToken").value = config.webhook_token || "";
  $("webhookTargetGroup").value = config.webhook_target_group || "";
  $("webhookPrefix").value = config.webhook_prefix || "";
  $("webhookTemplate").value = config.webhook_template || "";
  updateWebhookUrl();
  $("fallbackEnabled").checked = config.fallback_enabled ?? false;
  $("fallbackApiBaseUrl").value = config.fallback_api_base_url || "";
  $("fallbackModel").value = config.fallback_model || "";
  $("fallbackApiKey").value = "";
  $("clearFallbackApiKey").checked = false;
  $("fallbackApiKeyState").textContent = config.fallback_api_key_configured ? "已安全配置" : "未配置";
  $("offenseGuardEnabled").checked = config.offense_guard_enabled ?? false;
  $("offensePrompt").value = config.offense_prompt || "";
  $("offenseAction").value = config.offense_action || "mute";
  $("offenseMuteDuration").value = config.offense_mute_duration ?? 600;
  $("offenseThreshold").value = config.offense_threshold ?? 1;
  $("offenseIncludeAdmins").checked = config.offense_include_admins ?? false;
  $("jevEnabled").checked = config.jev_enabled ?? false;
  $("jevModel").value = config.jev_model || "jev-latest";
  $("jevTimeout").value = config.jev_timeout ?? 6;
  $("jevGateEnabled").checked = config.jev_gate_enabled ?? true;
  $("jevOffenseEnabled").checked = config.jev_offense_enabled ?? true;
  $("jevAddressedThreshold").value = config.jev_addressed_threshold ?? 0.8;
  $("jevWorthThreshold").value = config.jev_worth_threshold ?? 0.7;
  $("jevOffenseThreshold").value = config.jev_offense_threshold ?? 0.85;
  $("jevMaxIntrusion").value = config.jev_max_intrusion ?? 1.3;
  $("jevMinTextLength").value = config.jev_min_text_length ?? 4;
  $("jevContextMessages").value = config.jev_context_messages ?? 6;
  $("jevUseProactiveBudget").checked = config.jev_use_proactive_budget ?? true;
  $("jevLogEnabled").checked = config.jev_log_enabled ?? true;
  $("jevLogRetention").value = config.jev_log_retention ?? 500;
  $("jevAddressedPrompt").value = config.jev_addressed_prompt || "";
  $("jevInterestPrompt").value = config.jev_interest_prompt || "";
  $("jevInterestThreshold").value = config.jev_interest_threshold ?? 0.7;
  $("jevWorthPrompt").value = config.jev_worth_prompt || "";
  $("jevIntrusionLevels").value = (config.jev_intrusion_levels || []).join("\n");
  const prevJevGroup = $("jevLogGroupFilter").value;
  $("jevLogGroupFilter").replaceChildren(
    Object.assign(document.createElement("option"), { value: "", textContent: "全部白名单群" }),
    ...(config.allowed_groups || []).map((g) => Object.assign(document.createElement("option"), { value: String(g), textContent: String(g) })),
  );
  if ([...$("jevLogGroupFilter").options].some((o) => o.value === prevJevGroup)) $("jevLogGroupFilter").value = prevJevGroup;
  $("joinGateEnabled").checked = config.join_gate_enabled ?? false;
  $("joinGateGroup").value = config.join_gate_group || "";
  $("joinGateApiUrl").value = config.join_gate_api_url || "";
  $("joinGateMinLicenses").value = config.join_gate_min_licenses ?? 2;
  $("joinGateRefreshHours").value = config.join_gate_refresh_hours ?? 24;
  $("joinGateRejectReason").value = config.join_gate_reject_reason || "";
  $("joinGateToken").value = "";
  $("clearJoinGateToken").checked = false;
  $("joinGateTokenState").textContent = config.join_gate_token_configured ? "已安全配置" : "未配置";
  $("summaryEnabled").checked = config.summary_enabled ?? false;
  $("summarySendEnabled").checked = config.summary_send_enabled ?? true;
  $("summaryHour").value = config.summary_hour ?? 20;
  $("summaryMinMessages").value = config.summary_min_messages ?? 20;
  const prevSummaryGroup = $("summaryGroupSelect").value;
  $("summaryGroupSelect").replaceChildren(
    ...(config.allowed_groups || []).map((g) => Object.assign(document.createElement("option"), { value: String(g), textContent: String(g) })),
  );
  if ([...$("summaryGroupSelect").options].some((o) => o.value === prevSummaryGroup)) $("summaryGroupSelect").value = prevSummaryGroup;
  $("modelStatus").textContent = config.chat_configured ? config.model : "缺少 API Key";
  $("modelDot").className = `status-dot ${config.chat_configured ? "ok" : "bad"}`;
  updateCounters();
  markClean();
}

function updateCounters() {
  let groups = [];
  try { groups = parseIds($("allowedGroups").value, "群号"); } catch (_) { /* form handles it */ }
  $("groupCount").textContent = `${groups.length} 个群`;
  $("promptCount").textContent = `${$("systemPrompt").value.length} 字`;
  const ruleCount = document.querySelectorAll(".prompt-rule-card").length;
  $("promptRuleCount").textContent = `${ruleCount} 条规则`;
  $("promptRuleEmpty").classList.toggle("hidden", ruleCount > 0);
  $("temperatureValue").textContent = Number($("temperature").value).toFixed(1);
  $("topPValue").textContent = Number($("topP").value).toFixed(2);
  $("replyProbabilityValue").textContent = `${Math.round(Number($("replyProbability").value) * 100)}%`;
  const triggerKeywords = $("triggerKeywords").value.split("\n").map((item) => item.trim()).filter(Boolean);
  $("triggerKeywordCount").textContent = `${new Set(triggerKeywords).size} 个词`;
  $("quoteReplyProbabilityValue").textContent = `${Math.round(Number($("quoteReplyProbability").value) * 100)}%`;
  $("segmentProbabilityValue").textContent = `${Math.round(Number($("segmentProbability").value) * 100)}%`;
  const keywords = $("moderationKeywords").value.split("\n").map((item) => item.trim()).filter(Boolean);
  $("keywordCount").textContent = `${new Set(keywords).size} 个词`;
}

function markDirty() {
  $("saveButton").disabled = false;
  $("dirtyText").textContent = "有未保存的更改";
  $("saveState").textContent = "等待保存";
  updateCounters();
}

function markClean() {
  $("saveButton").disabled = true;
  $("dirtyText").textContent = "没有未保存的更改";
  $("saveState").textContent = "配置已同步";
  $("saveError").textContent = "";
}

function toast(message) {
  $("toast").textContent = message;
  $("toast").classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => $("toast").classList.remove("show"), 2600);
}

function updateWebhookUrl() {
  const value = $("webhookToken").value.trim();
  $("webhookUrl").value = value ? `${location.origin}/webhook/${value}` : "（先设置令牌并保存）";
}

function renderJoinGateStatus(s) {
  $("joinGateCount").textContent = s.count ?? 0;
  $("joinGateLoaded").textContent = s.loaded ? "已加载" : "未加载";
  $("joinGateDot").className = `status-dot ${s.loaded ? "ok" : "bad"}`;
  $("joinGateUpdated").textContent = s.updated_at || "—";
}

async function loadJoinGateStatus() {
  try { renderJoinGateStatus(await api("/admin/api/join-gate")); } catch (error) { /* status is best-effort */ }
}

async function loadSummaryImage(groupId, hasPng) {
  const img = $("summaryImg");
  if (img._url) { URL.revokeObjectURL(img._url); img._url = null; }
  if (!hasPng) { img.style.display = "none"; $("summaryImgEmpty").classList.remove("hidden"); return; }
  try {
    const response = await fetch(`/admin/api/summary/png?group_id=${groupId}&_=${Date.now()}`, { headers: authHeaders() });
    if (!response.ok) throw new Error("no image");
    const url = URL.createObjectURL(await response.blob());
    img._url = url; img.src = url; img.style.display = "block";
    $("summaryImgEmpty").classList.add("hidden");
  } catch (error) { img.style.display = "none"; $("summaryImgEmpty").classList.remove("hidden"); }
}

async function loadSummaryPreview() {
  const groupId = $("summaryGroupSelect").value;
  if (!groupId) { $("summaryPreviewMeta").textContent = "先在上方配置白名单群"; return; }
  try {
    const result = await api(`/admin/api/summary?group_id=${groupId}`);
    if (result.date) {
      $("summaryPreviewMeta").textContent = `群 ${groupId} · ${result.date}`;
      $("summaryFrame").srcdoc = result.html || "";
    } else {
      $("summaryPreviewMeta").textContent = `群 ${groupId} · 还没有生成过总结`;
      $("summaryFrame").srcdoc = "<p style='font-family:sans-serif;color:#888;padding:24px'>还没有生成过总结，点上方“立即生成”试试。</p>";
    }
    await loadSummaryImage(groupId, result.has_png);
  } catch (error) { toast(error.message); }
}

async function generateSummary(send) {
  const groupId = $("summaryGroupSelect").value;
  $("summaryError").textContent = "";
  if (!groupId) { $("summaryError").textContent = "请先选择预览群"; return; }
  const buttons = [$("genSummary"), $("genSendSummary")];
  buttons.forEach((b) => (b.disabled = true));
  $("summaryHint").textContent = "正在生成（走大模型 + 截图，可能要十几秒）…";
  try {
    const result = await api(`/admin/api/summary/generate?group_id=${groupId}&send=${send ? 1 : 0}`, { method: "POST" });
    $("summaryHint").textContent = send ? (result.sent ? "已生成并发送到群。" : "已生成，但发送失败（看日志）。") : "已生成。";
    toast(send ? "总结已生成并发送" : "总结已生成");
    await loadSummaryPreview();
  } catch (error) { $("summaryError").textContent = error.message; $("summaryHint").textContent = "设置会保存到本机运行配置，立即生效。"; }
  finally { buttons.forEach((b) => (b.disabled = false)); }
}

function memberStatusText(status) {
  return { ready: "画像已生成", processing: "分析中", failed: "分析失败", pending: "等待样本" }[status] || "等待样本";
}

function renderProfileTags(container, values) {
  const tags = (values || []).map((value) => Object.assign(document.createElement("span"), { textContent: value }));
  container.replaceChildren(...(tags.length ? tags : [Object.assign(document.createElement("span"), { textContent: "暂无" })]));
}

function renderMembers(members) {
  const cards = members.map((member) => {
    const card = document.createElement("button");
    card.type = "button";
    card.className = "member-card";

    const identity = document.createElement("div");
    const name = document.createElement("strong");
    name.textContent = member.display_name;
    const meta = document.createElement("small");
    meta.textContent = member.bot_nickname
      ? `QQ ${member.user_id} · 群 ${member.group_id} · 称呼 ${member.bot_nickname}`
      : `QQ ${member.user_id} · 群 ${member.group_id}`;
    identity.append(name, meta);

    const score = document.createElement("span");
    score.className = "member-score";
    score.textContent = `${Number(member.favorability).toFixed(1)}`;

    const messages = document.createElement("small");
    messages.textContent = `${member.message_count} 条消息`;

    const summary = document.createElement("span");
    summary.className = "member-summary";
    summary.textContent = member.profile_summary || "等待积累更多聊天样本";

    const status = document.createElement("span");
    status.className = `member-status ${member.analysis_status || "pending"}`;
    status.textContent = memberStatusText(member.analysis_status);
    card.append(identity, score, messages, summary, status);
    card.addEventListener("click", () => openMember(member.group_id, member.user_id));
    return card;
  });
  $("memberList").replaceChildren(...cards);
  $("memberEmpty").classList.toggle("hidden", cards.length > 0);
}

async function loadMembers() {
  const groupId = $("memberGroupFilter").value;
  const query = $("memberSearch").value.trim();
  const parameters = new URLSearchParams({ limit: "200" });
  if (groupId) parameters.set("group_id", groupId);
  if (query) parameters.set("query", query);
  const result = await api(`/admin/api/members?${parameters}`);
  renderMembers(result.members || []);
  $("memberStatCount").textContent = result.stats?.members ?? 0;
  $("memberStatMessages").textContent = result.stats?.messages ?? 0;
  $("memberStatAnalyzed").textContent = result.stats?.analyzed ?? 0;
}

// ---------------------------------------------------------------- Jev 判定日志

function jevLocalTime(iso) {
  // Stored as UTC ISO8601; show it in the operator's own timezone.
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return iso || "";
  return parsed.toLocaleString(undefined, { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function jevCell(value, crossed, digits = 2) {
  const cell = document.createElement("td");
  cell.className = `num ${crossed ? "hot" : "cold"}`;
  cell.textContent = Number(value).toFixed(digits);
  return cell;
}

function jevBadge(text, kind) {
  const badge = document.createElement("span");
  badge.className = `jev-badge ${kind}`;
  badge.textContent = text;
  return badge;
}

function renderJevLogs(items, config) {
  const body = $("jevLogBody");
  body.replaceChildren();
  $("jevLogEmpty").classList.toggle("hidden", items.length > 0);
  for (const item of items) {
    const row = document.createElement("tr");

    const when = document.createElement("td");
    when.className = "jev-log-meta";
    when.textContent = jevLocalTime(item.created_at);
    row.appendChild(when);

    const who = document.createElement("td");
    who.className = "jev-log-meta";
    who.textContent = `${item.display_name || "?"} · ${item.user_id ?? "?"}${item.group_id ? ` @ ${item.group_id}` : ""}`;
    row.appendChild(who);

    const text = document.createElement("td");
    text.className = "jev-text";
    text.textContent = item.text || "";
    row.appendChild(text);

    // Highlight the judgments that actually crossed their configured threshold.
    row.appendChild(jevCell(item.addressed, item.addressed >= config.addressed_threshold));
    row.appendChild(jevCell(item.worth, item.worth >= config.worth_threshold));
    row.appendChild(jevCell(item.interested ?? 0, (item.interested ?? 0) >= config.interest_threshold));
    row.appendChild(jevCell(item.intrusion, item.intrusion > config.max_intrusion));
    row.appendChild(jevCell(item.offensive, item.offensive >= config.offense_threshold));

    const outcome = document.createElement("td");
    outcome.appendChild(item.replied ? jevBadge("回复", "reply") : jevBadge("沉默", "silent"));
    if (item.hard_rule) outcome.appendChild(jevBadge("硬规则", "hard"));
    if (item.offended) outcome.appendChild(jevBadge("冒犯", "offended"));
    if (item.budget_blocked) outcome.appendChild(jevBadge("额度", "budget"));
    row.appendChild(outcome);

    const reason = document.createElement("td");
    reason.className = "jev-reason";
    reason.textContent = item.reason || "";
    row.appendChild(reason);

    body.appendChild(row);
  }
}

async function loadJevLogs() {
  const parameters = new URLSearchParams({
    limit: String(Number($("jevLogLimit").value) || 100),
    outcome: $("jevLogOutcome").value || "all",
    hours: String(Number($("jevLogHours").value) || 24),
  });
  const groupId = $("jevLogGroupFilter").value;
  if (groupId) parameters.set("group_id", groupId);
  const result = await api(`/admin/api/jev-logs?${parameters}`);
  const stats = result.stats || {};
  $("jevStatJudged").textContent = stats.judged ?? 0;
  $("jevStatReplied").textContent = stats.replied ?? 0;
  $("jevStatSilent").textContent = stats.silent ?? 0;
  $("jevStatOffended").textContent = stats.offended ?? 0;
  $("jevStatBudget").textContent = stats.budget_blocked ?? 0;
  $("jevStatStored").textContent = stats.stored ?? 0;
  renderJevLogs(result.items || [], result.config || {});
  if (result.config && result.config.jev_enabled === false) {
    $("jevLogEmpty").textContent = "Jev 判定当前未启用，不会产生新的判定记录。打开上面的总开关并保存即可开始记录。";
  } else if (result.config && result.config.jev_log_enabled === false) {
    $("jevLogEmpty").textContent = "判定日志记录已关闭，不会写入新记录。";
  } else {
    $("jevLogEmpty").textContent = "还没有判定记录。开启 Jev 判定后，白名单群里的消息会在这里留下判定痕迹。";
  }
}

function fillMemberDetail(member) {
  selectedMember = member;
  $("memberDetailName").textContent = member.display_name;
  $("memberDetailMeta").textContent = `QQ ${member.user_id} · 群 ${member.group_id} · ${member.message_count} 条消息 · ${memberStatusText(member.analysis_status)}`;
  $("memberFavorability").value = member.favorability;
  $("memberFavorabilityValue").textContent = Number(member.favorability).toFixed(1);
  $("memberFavorabilityReason").textContent = member.favorability_reason || "尚无模型评估说明，可由管理员手动调整。";
  $("memberProfileSummary").textContent = member.profile_summary || "尚未分析";
  $("memberCommunicationStyle").textContent = member.communication_style || "尚未分析";
  $("memberInteractionAdvice").textContent = member.interaction_advice || "尚未分析";
  renderProfileTags($("memberTraits"), member.personality_traits);
  renderProfileTags($("memberInterests"), member.interests);
  $("memberBotNickname").value = member.bot_nickname || "";
  $("memberOffenseCount").value = member.offense_count ?? 0;
  $("memberAdminNote").value = member.admin_note || "";
  const messages = (member.recent_messages || []).map((message) => {
    const item = document.createElement("article");
    const content = document.createElement("p");
    content.textContent = message.content;
    const time = document.createElement("time");
    time.textContent = message.created_at;
    item.append(content, time);
    return item;
  });
  $("memberRecentMessages").replaceChildren(...(messages.length ? messages : [Object.assign(document.createElement("p"), { className: "muted", textContent: "没有保留的消息" })]));
  $("memberActionError").textContent = member.analysis_error || "";
  $("memberDetail").classList.remove("hidden");
}

async function openMember(groupId, userId) {
  try {
    const result = await api(`/admin/api/members/${groupId}/${userId}`);
    fillMemberDetail(result.member);
    $("memberDetail").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) { toast(error.message); }
}

async function loadStatus() {
  const status = await api("/admin/api/status");
  const connected = status.connected_bots || [];
  $("napcatStatus").textContent = connected.length ? `${connected.length} 个账号已连接` : "尚未连接";
  $("napcatDot").className = `status-dot ${connected.length ? "ok" : "bad"}`;
  const memoryOk = ["ok", "degraded"].includes(status.memory?.status);
  $("memoryStatus").textContent = memoryOk ? status.memory.status : "不可用";
  $("memoryDot").className = `status-dot ${memoryOk ? "ok" : "bad"}`;
}

async function loadDashboard() {
  const [config] = await Promise.all([api("/admin/api/config"), loadStatus()]);
  fillConfig(config);
  showApp();
  if (location.hash === "#intelligence") await loadIntelligence();
  if (location.hash === "#routine") await loadRoutine();
  if (location.hash === "#requests") await loadRequests();
}

function switchPage(target) {
  document.querySelectorAll(".page-section").forEach((page) => {
    page.classList.toggle("active", page.dataset.page === target);
  });
  document.querySelectorAll(".nav-item").forEach((button) => {
    button.classList.toggle("active", button.dataset.target === target);
  });
  history.replaceState(null, "", `#${target}`);
  $("configSaveBar").classList.toggle("hidden", ["requests", "members", "humanize", "webhook", "joingate", "summary"].includes(target));
  if (target === "requests" && token()) loadRequests();
  if (target === "members" && token()) loadMembers().catch((error) => toast(error.message));
  if (target === "jev" && token()) loadJevLogs().catch((error) => toast(error.message));
  if (target === "intelligence" && token()) loadIntelligence();
  if (target === "routine" && token()) loadRoutine();
  if (target === "joingate" && token()) loadJoinGateStatus();
  if (target === "summary" && token()) loadSummaryPreview();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function updateSegmentDelayPreview() {
  const low = Math.min(Number($("segmentDelayMin").value), Number($("segmentDelayMax").value));
  const high = Math.max(Number($("segmentDelayMin").value), Number($("segmentDelayMax").value));
  $("segmentDelayPreview").textContent = high <= 0 ? "当前已关闭分段停顿" : "预计段间停顿：" + [10, 30, 60, 120].map((length) => {
    const seconds = Math.min(10, Math.max(0.7, (low + high) / 2 + 6.5 * (1 - Math.exp(-length / 55))));
    return `${length} 字 ≈ ${seconds.toFixed(1)} 秒`;
  }).join("　·　");
}
for (const id of ["segmentDelayMin", "segmentDelayMax"]) $(id).addEventListener("input", updateSegmentDelayPreview);

function requestDuration(ms) {
  if (ms == null) return "—";
  return ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(1)} s`;
}

function requestUsage(usage) {
  if (!usage) return "—";
  if (usage.total_tokens != null) return `${usage.total_tokens.toLocaleString()} tokens`;
  if (usage.seconds != null) return `${Number(usage.seconds).toFixed(1)} 秒`;
  if (usage.credits != null) return `${usage.credits} credits`;
  if (usage.input_tokens != null || usage.output_tokens != null) return `${usage.input_tokens ?? 0} → ${usage.output_tokens ?? 0}`;
  return "查看详情";
}

async function loadRequests() {
  if (!token()) return;
  const sequence = ++requestLoadSequence;
  const params = new URLSearchParams({ limit: String(REQUEST_PAGE_SIZE), offset: String(requestOffset) });
  for (const [id, key] of [["requestKind", "kind"], ["requestFeature", "feature"], ["requestStatus", "status"], ["requestGroup", "group_id"], ["requestSearch", "query"]]) {
    const value = $(id).value.trim();
    if (value) params.set(key, value);
  }
  if (params.has("group_id") && !/^\d+$/.test(params.get("group_id"))) {
    $("requestError").textContent = "群号只能填写数字";
    $("refreshRequests").disabled = false;
    return;
  }
  $("requestError").textContent = "";
  $("refreshRequests").disabled = true;
  try {
    const result = await api(`/admin/api/requests?${params}`);
    if (sequence !== requestLoadSequence || !token()) return;
    if (requestOffset > 0 && requestOffset >= result.total) {
      requestOffset = Math.max(0, Math.floor((result.total - 1) / REQUEST_PAGE_SIZE) * REQUEST_PAGE_SIZE);
      return loadRequests();
    }
    requestKinds = result.kinds || {};
    requestFeatures = result.features || {};
    const feature = $("requestFeature").value;
    $("requestFeature").replaceChildren(new Option("全部功能", ""), ...Object.entries(requestFeatures).map(([key, label]) => new Option(label, key)));
    $("requestFeature").value = feature;
    $("requestTotal").textContent = result.total.toLocaleString();
    $("requestSuccess").textContent = (result.stats?.success || 0).toLocaleString();
    $("requestFailed").textContent = (result.stats?.failed || 0).toLocaleString();
    $("requestLatency").textContent = requestDuration(result.stats?.average_ms);
    $("requestRows").replaceChildren(...(result.items || []).map((item) => {
      const row = document.createElement("tr");
      const cell = (value) => { const td = document.createElement("td"); td.textContent = value; row.append(td); return td; };
      cell(new Date(item.created_at).toLocaleString("zh-CN", { hour12: false }));
      const source = cell(requestFeatures[item.feature] || item.feature);
      const kind = document.createElement("small"); kind.textContent = requestKinds[item.kind] || item.kind; source.append(kind);
      cell(item.model || "—");
      const group = cell(item.group_id || (item.user_id ? "私聊" : "后台 / 系统"));
      if (item.user_id) { const user = document.createElement("small"); user.textContent = item.user_id; group.append(user); }
      const status = document.createElement("span"); status.className = `request-badge ${Object.hasOwn(requestStatuses, item.status) ? item.status : ""}`;
      status.textContent = requestStatuses[item.status] || item.status; cell("").append(status);
      cell(item.status === "running" ? "进行中" : requestDuration(item.duration_ms));
      cell(requestUsage(item.usage));
      const button = document.createElement("button"); button.type = "button"; button.className = "text-button";
      button.textContent = "查看"; button.setAttribute("aria-label", `查看请求 ${item.id}`);
      button.addEventListener("click", () => openRequestDetail(item.id)); cell("").append(button);
      return row;
    }));
    $("requestEmpty").classList.toggle("hidden", !!result.items?.length);
    $("requestEmpty").textContent = result.total ? "此页暂无记录" : "没有符合条件的请求。新调用完成后会显示在这里。";
    $("requestPageInfo").textContent = result.total ? `第 ${requestOffset + 1}–${requestOffset + result.items.length} 条，共 ${result.total} 条` : "共 0 条";
    $("requestPrev").disabled = requestOffset === 0;
    $("requestNext").disabled = requestOffset + REQUEST_PAGE_SIZE >= result.total;
  } catch (error) {
    if (sequence === requestLoadSequence) $("requestError").textContent = error.message;
  } finally {
    if (sequence === requestLoadSequence) $("refreshRequests").disabled = false;
  }
}

async function openRequestDetail(id) {
  const sequence = ++requestDetailSequence;
  $("requestDetailTitle").textContent = `请求 #${id}`;
  $("requestDetailMeta").textContent = "正在读取详情…";
  for (const field of ["requestInput", "requestOutput", "requestUsage", "requestDetailError", "requestScoreSummary"]) $(field).textContent = "";
  if (!$("requestDetail").open) $("requestDetail").showModal();
  try {
    const item = await api(`/admin/api/requests/${id}`);
    if (sequence !== requestDetailSequence || !token()) return;
    $("requestDetailTitle").textContent = `${requestFeatures[item.feature] || item.feature} · #${id}`;
    $("requestDetailMeta").textContent = `${new Date(item.created_at).toLocaleString("zh-CN", { hour12: false })} · ${item.model || requestKinds[item.kind]} · ${requestStatuses[item.status] || item.status} · ${requestDuration(item.duration_ms)}`;
    $("requestDetailError").textContent = item.error || "";
    const json = (value) => JSON.stringify(value, null, 2) ?? "暂无";
    $("requestInput").textContent = json(item.request);
    $("requestOutput").textContent = item.status === "running" ? "请求进行中，完成后重新打开即可查看结果。" : json(item.response);
    $("requestUsage").textContent = json({ usage: item.usage, trace_id: item.trace_id, group_id: item.group_id, user_id: item.user_id, endpoint: item.endpoint || "SDK 托管" });
    if (item.kind === "jev") {
      const labels = { reply_complete: "核心短句完整度", reply_tail: "尾句多余度", search_needed: "联网必要性", casual_typing: "闲聊手误适合度", routine_photo_suitable: "配图适合度", addressed: "对机器人说话", worth: "回复价值", voice_suitable: "语音适合度", offensive: "冒犯程度", intrusion: "插话程度" };
      const prefixes = item.request?.state?.prefixes;
      if (prefixes) {
        const selected = Object.keys(prefixes).find(key => {
          const index = key.replace("prefix_", "");
          return ["reply_complete", "reply_tail"].every(kind => { const score = item.response?.[`${kind}_${index}`]?.noul; const threshold = item.request?.thresholds?.[kind] ?? (kind === "reply_complete" ? 0.8 : 0.5); return score >= threshold && score <= 1; });
        });
        const badge = document.createElement("span");
        badge.textContent = selected ? `短回复收口：${prefixes[selected]}` : "短回复收口：保留原文";
        $("requestScoreSummary").append(badge);
      }
      for (const [name, answer] of Object.entries(item.response || {})) {
        if (!answer || typeof answer !== "object") continue;
        if (name === "reply_depth" && answer.choice) {
          const badge = document.createElement("span");
          const depthLabels = { minimal: "一句收口", normal: "按需简答", detailed: "完整展开" };
          badge.textContent = `回复详略：${depthLabels[answer.choice] || answer.choice}${typeof answer.confidence === "number" ? ` · 置信度 ${answer.confidence.toFixed(2)}${answer.confidence < 0.8 ? "，回退按需简答" : ""}` : ""}`;
          $("requestScoreSummary").append(badge);
          continue;
        }
        const score = answer.noul ?? answer.score;
        if (typeof score !== "number") continue;
        const badge = document.createElement("span");
        const key = name.replace(/_\d+$/, "");
        const threshold = item.request?.thresholds?.[name === "routine_photo_suitable" ? "photo" : key];
        badge.textContent = `${labels[key] || name}${key !== name ? ` ${Number(name.split("_").at(-1)) + 1}` : ""} ${score.toFixed(2)}${typeof threshold === "number" ? ` / 阈值 ${threshold.toFixed(2)}` : ""}`;
        $("requestScoreSummary").append(badge);
      }
    }
  } catch (error) {
    if (sequence === requestDetailSequence) $("requestDetailError").textContent = error.message;
  }
}

$("closeRequestDetail").addEventListener("click", () => $("requestDetail").close());
$("requestDetail").addEventListener("close", () => { requestDetailSequence++; });
$("refreshRequests").addEventListener("click", () => loadRequests());
$("requestPrev").addEventListener("click", () => { requestOffset = Math.max(0, requestOffset - REQUEST_PAGE_SIZE); loadRequests(); });
$("requestNext").addEventListener("click", () => { requestOffset += REQUEST_PAGE_SIZE; loadRequests(); });
for (const id of ["requestKind", "requestFeature", "requestStatus"]) $(id).addEventListener("change", () => { requestOffset = 0; loadRequests(); });
for (const id of ["requestGroup", "requestSearch"]) $(id).addEventListener("input", () => {
  clearTimeout(requestSearchTimer);
  requestSearchTimer = setTimeout(() => { requestOffset = 0; loadRequests(); }, 300);
});
setInterval(() => {
  if (token() && !document.hidden && !$("appView").classList.contains("hidden") && location.hash === "#requests" && !$("refreshRequests").disabled) loadRequests();
}, 15000);

function clearVoicePreview() {
  $("voicePreviewAudio").pause();
  $("voicePreviewAudio").removeAttribute("src");
  $("voicePreviewAudio").load();
  if (voicePreviewUrl) URL.revokeObjectURL(voicePreviewUrl);
  voicePreviewUrl = null;
  $("voicePreviewResult").classList.add("hidden");
}

function collectSearchSettings() {
  return {search_api_base_url: $("searchBaseUrl").value.trim(), search_max_results: Number($("searchMaxResults").value),
    search_timeout: Number($("searchTimeout").value), search_api_key: $("searchApiKey").value.trim() || null,
    clear_search_api_key: $("clearSearchKey").checked};
}
$("toggleSearchKey").addEventListener("click", () => {
  const input = $("searchApiKey"); input.type = input.type === "password" ? "text" : "password";
  $("toggleSearchKey").textContent = input.type === "password" ? "显示" : "隐藏";
});
for (const type of ["input", "change"]) $("searchPreviewQuery").addEventListener(type, (event) => event.stopPropagation());
$("previewSearch").addEventListener("click", async () => {
  const query = $("searchPreviewQuery").value.trim();
  if (query.length < 2) {
    $("searchPreviewError").textContent = "请输入至少两个字的搜索词。";
    $("searchPreviewStatus").textContent = "";
    return;
  }
  $("previewSearch").disabled = true;
  $("searchPreviewError").textContent = "";
  $("searchPreviewStatus").textContent = "正在搜索…";
  $("searchPreviewResults").replaceChildren();
  try {
    const result = await api("/admin/api/search/preview", {method:"POST", body:JSON.stringify({...collectSearchSettings(), query})});
    if (!token()) return;
    for (const row of result.results || []) {
      const article = document.createElement("article"), link = document.createElement("a"), summary = document.createElement("p"), address = document.createElement("small");
      const url = new URL(row.url); if (!["http:","https:"].includes(url.protocol)) continue;
      link.textContent = row.title; link.href = url.href; link.target = "_blank"; link.rel = "noopener noreferrer";
      summary.textContent = row.content; address.textContent = [row.published_date, row.url].filter(Boolean).join(" · ");
      article.append(link, summary, address); $("searchPreviewResults").append(article);
    }
    $("searchPreviewStatus").textContent = result.results?.length ? `找到 ${result.results.length} 个来源。` : "本次没有找到可用结果。";
  } catch (error) {
    $("searchPreviewError").textContent = error.message;
    $("searchPreviewStatus").textContent = "搜索未完成";
  } finally { $("previewSearch").disabled = false; }
});

const voicePresets = {
  voicePresetSweet: { voice: "gleam", pace: "natural", instructions: "声音柔和明亮，带一点笑意，甜而自然，像和熟悉的朋友轻声聊天。语调有轻微起伏，停顿自然，不夹嗓、不用播音腔、不夸张撒娇；不额外添加原文没有的台词。" },
  voicePresetSoft: { voice: "willow", pace: "slow", instructions: "声音温柔放松，语气亲近，从容地说话，有恰当的停顿，像认真倾听的朋友。保持清晰，不刻意耳语，不用客服腔，不额外添加台词。" },
  voicePresetBright: { voice: "gleam", pace: "brisk", instructions: "声音明亮，语气轻快、有活力，像开心地与朋友分享消息。节奏轻盈，吐字清楚，不过度兴奋或表演，不额外添加台词。" },
};
for (const [id, preset] of Object.entries(voicePresets)) {
  $(id).addEventListener("click", () => {
    $("voiceName").value = preset.voice;
    $("voicePace").value = preset.pace;
    $("voiceInstructions").value = preset.instructions;
    markDirty();
    toast("已应用风格，可以生成试听后再保存");
  });
}
$("toggleVoiceApiKey").addEventListener("click", () => {
  const input = $("voiceApiKey");
  input.type = input.type === "password" ? "text" : "password";
  $("toggleVoiceApiKey").textContent = input.type === "password" ? "显示" : "隐藏";
});
for (const type of ["input", "change"]) {
  for (const id of ["voicePreviewText", "voicePreviewContext"]) {
    $(id).addEventListener(type, (event) => event.stopPropagation());
  }
}
$("previewVoice").addEventListener("click", async () => {
  $("previewVoice").disabled = true;
  $("voicePreviewError").textContent = "";
  $("voicePreviewStatus").textContent = "正在判定并生成语音，完成后会自动关闭 Live 会话…";
  clearVoicePreview();
  try {
    const result = await api("/admin/api/voice/preview", {
      method: "POST", body: JSON.stringify({ ...collectVoiceSettings(), text: $("voicePreviewText").value.trim(), context: $("voicePreviewContext").value.trim() }),
    });
    if (!token()) return;
    if (result.voice_allowed === false) {
      $("voicePreviewStatus").textContent = result.voice_reason || "本轮适合文字，未生成语音。";
      return;
    }
    const bytes = Uint8Array.from(atob(result.audio_base64), (character) => character.charCodeAt(0));
    voicePreviewUrl = URL.createObjectURL(new Blob([bytes], { type: result.media_type }));
    $("voicePreviewAudio").src = voicePreviewUrl;
    $("voicePreviewTranscript").textContent = result.transcript || "";
    $("voicePreviewResult").classList.remove("hidden");
    $("voicePreviewStatus").textContent = `${result.voice_style || "固定风格"} · 已生成 ${result.duration_seconds} 秒语音。${result.voice_reason || "试听结果仅保留在本页。"}`;
    await $("voicePreviewAudio").play().catch(() => { $("voicePreviewStatus").textContent += " 点击播放器播放。"; });
  } catch (error) {
    $("voicePreviewError").textContent = error.message;
    $("voicePreviewStatus").textContent = "试听未完成，请检查配置后重试。";
  } finally {
    $("previewVoice").disabled = false;
  }
});
window.addEventListener("pagehide", clearVoicePreview);

const RECORD_PAGE_SIZE = 15;
const recordLabels = {active:"当前有效",superseded:"已更新",forgotten:"已忘记",open:"未完成",resolved:"已结束",pending:"待发送",sending:"发送中",sent:"已发送",cancelled:"已取消",failed:"发送失败",expired:"已过期",blocked:"权限已关闭",uncertain:"发送结果待核对",outdated:"已停用",disabled:"已取消",review:"超期，暂不引用"};
const recordKinds = {
  facts: {name:"长期事实", content:"事实内容", time:"记录时间", active:"active", action:"忘记", hint:"长期事实与纠错历史。已忘记的内容不再参与回复。"},
  topics: {name:"事项", content:"事项内容", time:"最近更新", active:"open", action:"标为已结束", hint:"查看未完成事项、跟进状态与已结束的历史记录。"},
  reminders: {name:"提醒", content:"提醒内容", time:"提醒时间", active:"pending", action:"取消提醒", hint:"按提醒设置的时区显示时间；只有待发送的提醒可以取消。"},
  knowledge: {name:"群知识库", content:"问题 / 已确认方案", time:"记录时间", active:"active", action:"停用方案", hint:"完整方案与原消息来源在详情中查看；超期知识暂不自动引用。"},
  preferences: {name:"回复偏好与群规则", content:"偏好 / 适用范围", time:"记录时间", active:"active", action:"取消偏好", hint:"个人偏好只作用于本人在当前会话的回复；群规则优先。"},
};

function recordNode(tag, text = "", className = "") {
  const node = document.createElement(tag); node.textContent = text; node.className = className; return node;
}
function recordStatus(kind, row) {
  const maxAge = Number(initialConfig?.jev_knowledge_max_age_days);
  return kind === "knowledge" && row.status === "active" && maxAge > 0 && Date.now() - new Date(row.created_at).getTime() > maxAge * 86400000 ? "review" : row.status;
}
function recordGroup(row) {
  const group = /^agent:qq-group-(\d+):group:\d+$/.exec(row.scope)?.[1] || row.group_id;
  return group ? `群 ${group}` : "私聊";
}
function recordDate(value, zone) {
  if (!value || Number.isNaN(new Date(value).getTime())) return "—";
  try { return new Date(value).toLocaleString("zh-CN", {hour12:false, timeZone:zone || initialConfig?.context_timezone}); }
  catch { return new Date(value).toLocaleString("zh-CN", {hour12:false}); }
}
function recordPreference(row) {
  const topic = {all:"所有回复",code:"代码问题",technical:"技术问题"}[row.topic] || row.topic;
  const mode = {text:"使用文字",auto:"自动选择"}[row.mode] || row.mode;
  return `${row.target_user_id === 0 ? "全群规则" : "个人偏好"} · ${topic} · ${mode}`;
}

function renderIntelligence() {
  const kind = intelligenceKind, spec = recordKinds[kind], data = intelligenceData?.[kind] || [];
  for (const tab of $("intelligenceTabs").querySelectorAll("[role=tab]")) {
    const selected = tab.dataset.kind === kind;
    tab.setAttribute("aria-selected", String(selected)); tab.tabIndex = selected ? 0 : -1;
    tab.querySelector("span").textContent = intelligenceData ? intelligenceData[tab.dataset.kind].length : "—";
    if (selected) $("intelligenceTablePanel").setAttribute("aria-labelledby", tab.id);
  }
  $("intelligenceTablePanel").querySelector("table").setAttribute("aria-label", spec.name);
  $("intelligenceContentHeading").textContent = spec.content;
  $("intelligenceTimeHeading").textContent = spec.time;
  $("intelligenceHint").textContent = spec.hint;
  if (intelligenceData) {
    const selectedStatus = $("intelligenceStatus").value;
    const statuses = new Set(data.map(row => recordStatus(kind, row)));
    if (selectedStatus) statuses.add(selectedStatus);
    $("intelligenceStatus").replaceChildren(new Option("全部状态", ""), ...[...statuses].map(status => new Option(recordLabels[status] || status, status)));
    $("intelligenceStatus").value = selectedStatus;
  }
  const status = $("intelligenceStatus").value, query = $("intelligenceSearch").value.trim().toLocaleLowerCase();
  const rows = data.filter(row => (!status || recordStatus(kind, row) === status) && (!query ||
    [row.text, row.question, row.display_name, row.user_id, row.target_user_id, `#${row.id}`, recordGroup(row)].join(" ").toLocaleLowerCase().includes(query)));
  intelligencePage = Math.min(intelligencePage, Math.max(0, Math.ceil(rows.length / RECORD_PAGE_SIZE) - 1));
  const offset = intelligencePage * RECORD_PAGE_SIZE;
  $("intelligenceRows").replaceChildren(...rows.slice(offset, offset + RECORD_PAGE_SIZE).map(row => {
    const tr = document.createElement("tr");
    const cell = (text = "", className = "") => { const td = recordNode("td", text, className); tr.append(td); return td; };
    cell(`#${row.id}`, "num muted");
    const content = cell();
    if (row.question) content.append(recordNode("p", row.question, "record-excerpt record-question"));
    content.append(recordNode("p", row.text, "record-excerpt"));
    if (kind === "preferences") content.append(recordNode("small", recordPreference(row)));
    if (row.sources?.length) content.append(recordNode("small", `${row.sources.length} 条原消息来源`));
    const owner = cell(recordGroup(row));
    owner.append(recordNode("small", `${row.display_name || "未命名"} · ${row.user_id}`));
    const state = recordStatus(kind, row);
    const color = ["active","open","sent"].includes(state) ? "success" : ["failed","uncertain","blocked"].includes(state) ? "error" : ["pending","sending","review"].includes(state) ? "running" : "";
    const statusCell = cell(); statusCell.append(recordNode("span", recordLabels[state] || state, `request-badge ${color}`));
    if (row.asked_at) statusCell.append(recordNode("small", "已追问"));
    if (row.last_error) statusCell.append(recordNode("small", "详情中查看原因"));
    const stamp = kind === "reminders" ? row.due_at : kind === "topics" ? row.updated_at || row.created_at : row.created_at;
    const time = cell(recordDate(stamp, row.timezone).replace(" ", "\n"), "record-time");
    if (row.timezone) time.append(recordNode("small", row.timezone));
    const actions = recordNode("div", "", "record-actions"); cell().append(actions);
    const detail = recordNode("button", "详情", "text-button"); detail.type = "button"; detail.setAttribute("aria-label", `查看${spec.name} ${row.id}`);
    detail.addEventListener("click", () => openIntelligenceDetail(kind, row)); actions.append(detail);
    if (row.status === spec.active) {
      const button = recordNode("button", spec.action, "text-button"); button.type = "button"; button.setAttribute("aria-label", `${spec.action} ${row.id}`);
      button.addEventListener("click", async () => {
        button.disabled = true;
        try { await api(`/admin/api/intelligence/${kind}/${row.id}/close`, {method:"POST"}); toast(`${spec.name} #${row.id} 已更新`); await loadIntelligence(); }
        catch (error) { $("intelligenceError").textContent = error.message; button.disabled = false; }
      }); actions.append(button);
    }
    return tr;
  }));
  $("intelligenceTablePanel").querySelector(".record-table-wrap").scrollTop = 0;
  $("intelligenceEmpty").classList.toggle("hidden", rows.length > 0);
  $("intelligenceEmpty").textContent = !intelligenceData ? ($("intelligenceError").textContent ? "记录未加载，请刷新重试。" : "正在读取记录…") : data.length ? "没有符合条件的记录。" : `暂无${spec.name}记录。`;
  $("intelligencePageInfo").textContent = !intelligenceData ? "" : rows.length ? `第 ${offset + 1}–${Math.min(offset + RECORD_PAGE_SIZE, rows.length)} 条，共 ${rows.length} 条` : "共 0 条";
  $("intelligencePrev").disabled = intelligencePage === 0;
  $("intelligenceNext").disabled = offset + RECORD_PAGE_SIZE >= rows.length;
}

function openIntelligenceDetail(kind, row) {
  $("intelligenceDetailTitle").textContent = `${recordKinds[kind].name} · #${row.id}`;
  const meta = $("intelligenceDetailMeta"), content = $("intelligenceDetailContent"); meta.replaceChildren(); content.replaceChildren();
  const field = (label, value) => { if (value != null && value !== "") meta.append(recordNode("dt", label), recordNode("dd", value)); };
  field("所属会话", recordGroup(row)); field("记录人", `${row.display_name || "未命名"} · QQ ${row.user_id}`);
  field("状态", recordLabels[recordStatus(kind, row)] || row.status); field("创建时间", recordDate(row.created_at, row.timezone));
  if (row.updated_at) field("更新时间", recordDate(row.updated_at, row.timezone));
  if (row.due_at) field("提醒时间", recordDate(row.due_at, row.timezone));
  if (row.asked_at) field("上次追问", recordDate(row.asked_at, row.timezone));
  field("时区", row.timezone || initialConfig?.context_timezone); field("来源消息", row.source_event);
  if (row.superseded_by) field("替代记录", `#${row.superseded_by}`);
  if (kind === "preferences") {
    field("适用对象", row.target_user_id === 0 ? "当前群全体成员" : `QQ ${row.target_user_id}`); field("规则", recordPreference(row));
  }
  const section = (title, text) => { const part = document.createElement("section"); part.append(recordNode("h3", title)); if (text) part.append(recordNode("p", text)); content.append(part); return part; };
  if (row.question) section("原问题", row.question);
  section(kind === "knowledge" ? "已确认方案" : "完整内容", row.text);
  if (row.last_error) section("发送结果说明", row.last_error);
  if (row.sources?.length) {
    const sources = section(`原消息来源 · ${row.sources.length} 条`);
    for (const source of row.sources) {
      const item = document.createElement("article");
      item.append(recordNode("small", `${source.speaker || "未知成员"} · ${recordDate(source.created_at)} · 消息 ${source.message_id || "—"}`, "muted"), recordNode("p", source.text || "")); sources.append(item);
    }
  }
  $("intelligenceDetail").showModal();
}

async function loadIntelligence() {
  const sequence = ++intelligenceLoadSequence;
  $("intelligenceError").textContent = ""; $("refreshIntelligence").disabled = true;
  $("intelligenceTablePanel").setAttribute("aria-busy", "true");
  intelligenceData = null; renderIntelligence();
  try {
    const group = $("intelligenceGroup").value.trim();
    if (group && !/^[1-9]\d*$/.test(group)) throw new Error("群号需要填写正整数。");
    const result = await api("/admin/api/intelligence" + (group ? `?group_id=${encodeURIComponent(group)}` : ""));
    if (sequence !== intelligenceLoadSequence || !token()) return;
    intelligenceData = Object.fromEntries(Object.keys(recordKinds).map(kind => [kind, Array.isArray(result[kind]) ? result[kind] : []]));
    renderIntelligence();
  } catch (error) {
    if (sequence === intelligenceLoadSequence) { $("intelligenceError").textContent = error.message; renderIntelligence(); }
  } finally {
    if (sequence === intelligenceLoadSequence) { $("refreshIntelligence").disabled = false; $("intelligenceTablePanel").setAttribute("aria-busy", "false"); }
  }
}
for (const tab of $("intelligenceTabs").querySelectorAll("[role=tab]")) {
  tab.addEventListener("click", () => {
    intelligenceKind = tab.dataset.kind; intelligencePage = 0;
    $("intelligenceStatus").value = ""; $("intelligenceSearch").value = "";
    renderIntelligence();
  });
  tab.addEventListener("keydown", event => {
    if (!["ArrowLeft","ArrowRight","Home","End"].includes(event.key)) return;
    event.preventDefault();
    const tabs = [...$("intelligenceTabs").querySelectorAll("[role=tab]")], index = tabs.indexOf(tab);
    const next = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : (index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
    tabs[next].focus(); tabs[next].click();
  });
}
for (const id of ["intelligenceGroup", "intelligenceStatus", "intelligenceSearch"]) {
  for (const type of ["input", "change"]) $(id).addEventListener(type, event => event.stopPropagation());
  $(id).addEventListener("keydown", event => { if (event.key === "Enter") { event.preventDefault(); if (id === "intelligenceGroup") loadIntelligence(); } });
}
$("intelligenceGroup").addEventListener("change", () => { intelligencePage = 0; loadIntelligence(); });
$("intelligenceStatus").addEventListener("change", () => { intelligencePage = 0; renderIntelligence(); });
$("intelligenceSearch").addEventListener("input", () => { intelligencePage = 0; renderIntelligence(); });
$("intelligencePrev").addEventListener("click", () => { intelligencePage--; renderIntelligence(); });
$("intelligenceNext").addEventListener("click", () => { intelligencePage++; renderIntelligence(); });
$("refreshIntelligence").addEventListener("click", loadIntelligence);
$("closeIntelligenceDetail").addEventListener("click", () => $("intelligenceDetail").close());
async function loadRoutine() {
  const sequence = ++routineLoadSequence;
  routinePlan = null;
  renderRoutineWake();
  try {
    const plan = await api(`/admin/api/routine?offset=${routineOffset}`);
    if (sequence !== routineLoadSequence) return;
    routinePlan = plan;
    renderRoutineWake();
    $("routineError").textContent = "";
    $("routineDate").textContent = `${plan.date} · ${plan.weekday} · ${plan.day_type} · ${plan.timezone}`;
    const events = Array.isArray(plan.events) ? plan.events : [];
    const theme = plan.theme || plan.day_type;
    $("routineTheme").textContent = `${theme} · ${{fixed: "固定时间", natural: "自然变化", rich: "丰富变化"}[plan.variation] || "固定时间"}`;
    $("routineFocus").textContent = plan.focus || "";
    $("routineEvents").textContent = events.length ? `生活小事：${events.map((event) => `${event.start} ${event.activity}`).join("；")}` : "今天没有额外安排生活小事。";
    $("routineState").textContent = !plan.enabled ? "未启用 · 仅预览" : routineOffset ? "明日计划" : `${plan.local_time} · ${plan.silent ? "睡眠静默，不回复" : "当前状态"}`;
    $("routineActivity").textContent = plan.current?.activity || `明天 · ${theme}`;
    $("routineRhythm").textContent = plan.current?.rhythm || "未来的安排仅作为计划，聊天时不会当成已经发生。";
    $("routineToday").setAttribute("aria-pressed", String(routineOffset === 0));
    $("routineTomorrow").setAttribute("aria-pressed", String(routineOffset === 1));
    $("routineTimeline").replaceChildren(...plan.slots.map((slot, index) => {
      const row = document.createElement("li");
      if (index === plan.current_index) { row.className = "current"; row.setAttribute("aria-current", "step"); }
      const time = document.createElement("span"); time.className = "routine-time"; time.textContent = `${slot.start}–${slot.end}`;
      if (slot.base_start && slot.base_start !== slot.start) time.title = `模板时间 ${slot.base_start}，今天调整为 ${slot.start}`;
      const kind = document.createElement("span"); kind.className = "count-pill"; kind.textContent = slot.is_event ? "小事" : slot.kind;
      const activity = document.createElement("span"); activity.textContent = slot.activity;
      row.append(time, kind, activity); return row;
    }));
    const diary = Array.isArray(plan.diary) ? plan.diary : [];
    if ($("routineDiary")) {
      $("routineDiaryState").textContent = routineOffset ? "明天的活动还是计划，到点后再记录细节。" :
        diary.length ? "到点补充一次，后续回复沿用这些事实；照片首次生成后保存原图。" : "还没有已保存的生活细节。启用日常后，活动开始时会自动记录。";
      $("routineDiary").replaceChildren(...diary.map((event) => {
        const row = document.createElement("li");
        const time = document.createElement("span"); time.className = "routine-time"; time.textContent = `${event.start}–${event.end}`;
        const status = document.createElement("span"); status.className = "count-pill"; status.textContent = event.has_photo ? "已保存原图" : event.status;
        const details = document.createElement("span"); details.textContent = event.details;
        row.append(time, status, details); return row;
      }));
    }
  } catch (error) { if (sequence === routineLoadSequence) $("routineError").textContent = error.message; }
}
$("routineToday").addEventListener("click", () => { routineOffset = 0; loadRoutine(); });
$("routineTomorrow").addEventListener("click", () => { routineOffset = 1; loadRoutine(); });
$("refreshRoutine").addEventListener("click", loadRoutine);
function renderRoutineWake() {
  const plan = routinePlan;
  $("forceWake").disabled = routineWakePending || !plan?.enabled || routineOffset !== 0 || plan.current?.kind !== "睡觉";
  const wake = (Array.isArray(plan?.wakeups) ? plan.wakeups : []).find(item => item.event_id === plan.current?.wake_origin_id);
  $("routineWakeState").textContent = !plan ? "" : !plan.enabled ? "开启并保存校园日常后，可以在睡眠时叫醒。" :
    routineOffset ? "强制起床针对当前睡眠，请切换到今天操作。" : wake ?
    `已于 ${wake.at.replace("T", " ").slice(0, 16)} 叫醒 · 理由：「${wake.reason}」。本次睡眠已结束，后续按原作息安排。` :
    plan.current?.kind === "睡觉" ? "现在正在睡觉，可以填写理由后强制起床。" : "当前已经清醒，无需强制起床。";
}
$("forceWake").addEventListener("click", () => {
  if ($("forceWake").disabled) return;
  $("routineWakeForm").reset();
  $("routineWakeError").textContent = "";
  $("routineWakeTitle").textContent = `叫${initialConfig?.bot_name || "小可"}起床`;
  $("routineWakeDialog").showModal();
  $("routineWakeReason").focus();
});
for (const id of ["closeRoutineWake", "cancelRoutineWake"]) $(id).addEventListener("click", () => {
  if (!routineWakePending) $("routineWakeDialog").close();
});
$("routineWakeDialog").addEventListener("cancel", event => { if (routineWakePending) event.preventDefault(); });
$("routineWakeForm").addEventListener("submit", async event => {
  event.preventDefault();
  if (routineWakePending) return;
  const reason = $("routineWakeReason").value.trim();
  if (!reason || [...reason].length > 300) {
    $("routineWakeError").textContent = "请填写 1–300 字的起床理由。";
    $("routineWakeReason").focus();
    return;
  }
  routineWakePending = true;
  $("routineWakeError").textContent = "";
  $("submitRoutineWake").textContent = "正在叫醒并记录…";
  for (const id of ["submitRoutineWake", "closeRoutineWake", "cancelRoutineWake", "routineWakeReason"]) $(id).disabled = true;
  renderRoutineWake();
  try {
    await api("/admin/api/routine/wake", { method: "POST", body: JSON.stringify({ reason }) });
    if (!$("routineWakeDialog").open) return;
    $("routineWakeDialog").close();
    routineOffset = 0;
    await loadRoutine();
    toast("已起床，起床时间和理由已记录");
  } catch (error) {
    if ($("routineWakeDialog").open) $("routineWakeError").textContent = error.message;
  } finally {
    routineWakePending = false;
    $("submitRoutineWake").textContent = "确认起床并记录";
    for (const id of ["submitRoutineWake", "closeRoutineWake", "cancelRoutineWake", "routineWakeReason"]) $(id).disabled = false;
    renderRoutineWake();
  }
});
function collectRoutinePhotoSettings() {
  return {
    routine_photo_api_base_url: $("routinePhotoBaseUrl").value.trim(),
    routine_photo_api_key: $("routinePhotoApiKey").value.trim() || null,
    clear_routine_photo_api_key: $("clearRoutinePhotoApiKey").checked,
    routine_photo_model: $("routinePhotoModel").value.trim(),
    routine_photo_quality: $("routinePhotoQuality").value,
    routine_photo_ratio: $("routinePhotoRatio").value,
    routine_photo_campus: $("routinePhotoCampus").value.trim(),
  };
}
for (const type of ["input", "change"]) {
  $("routinePhotoPreviewScene").addEventListener(type, (event) => event.stopPropagation());
}
$("previewRoutinePhoto").addEventListener("click", async () => {
  $("previewRoutinePhoto").disabled = true;
  $("routinePhotoError").textContent = "";
  $("routinePhotoPreviewStatus").textContent = "正在生成眼前场景的试拍，通常需要几十秒…";
  $("routinePhotoPreview").classList.add("hidden");
  $("routinePhotoPreview").removeAttribute("src");
  try {
    const result = await api("/admin/api/routine/photo/preview", { method: "POST", body: JSON.stringify({
      ...collectRoutinePhotoSettings(), scene: $("routinePhotoPreviewScene").value,
    }) });
    if (!token()) return;
    $("routinePhotoPreview").src = `data:image/jpeg;base64,${result.image_base64}`;
    $("routinePhotoPreview").classList.remove("hidden");
    $("routinePhotoPreviewStatus").textContent = `${result.time} · ${result.activity} · ${result.size} · ${result.model}`;
  } catch (error) {
    $("routinePhotoError").textContent = error.message;
    $("routinePhotoPreviewStatus").textContent = "试拍未完成，可检查配置后重试。";
  } finally { $("previewRoutinePhoto").disabled = false; }
});
setInterval(() => {
  if (location.hash === "#routine" && !document.hidden && token()) loadRoutine();
}, 60000);
function updateVisionMode() {
  const direct = $("visionMode").value === "direct";
  $("visionLegacyFields").classList.toggle("hidden", direct);
  $("visionDirectFields").classList.toggle("hidden", !direct);
}
$("visionMode").addEventListener("change", updateVisionMode);
for (const type of ["input", "change"]) $("intelligenceGroup").addEventListener(type, (event) => event.stopPropagation());

document.querySelectorAll(".nav-item").forEach((button) => {
  button.addEventListener("click", () => switchPage(button.dataset.target));
});

$("refreshJevLogs").addEventListener("click", () => {
  loadJevLogs().catch((error) => toast(error.message));
});

// These four only scope the log query. They sit inside #configForm, whose input/change
// handler calls markDirty(), so their events must not reach it -- otherwise merely
// changing "显示条数" would claim there are unsaved config edits.
for (const id of ["jevLogGroupFilter", "jevLogOutcome", "jevLogHours", "jevLogLimit"]) {
  const control = $(id);
  const stop = (event) => event.stopPropagation();
  control.addEventListener("input", stop);
  control.addEventListener("change", (event) => {
    event.stopPropagation();
    loadJevLogs().catch((error) => toast(error.message));
  });
}

$("clearJevLogs").addEventListener("click", async () => {
  if (!confirm("确定清空所有 Jev 判定日志？该操作不可撤销。")) return;
  try {
    const result = await api("/admin/api/jev-logs", { method: "DELETE" });
    toast(`已清空 ${result.removed ?? 0} 条判定日志`);
    await loadJevLogs();
  } catch (error) {
    toast(error.message);
  }
});

$("addPromptRule").addEventListener("click", () => {
  $("promptRuleList").appendChild(createPromptRule());
  updateCounters();
  markDirty();
});

$("loginForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("loginError").textContent = "";
  const value = $("adminToken").value.trim();
  try {
    const response = await fetch("/admin/api/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ token: value }) });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.detail || "管理令牌无效");
    sessionStorage.setItem(TOKEN_KEY, value);
    $("adminToken").value = "";
    await loadDashboard();
  } catch (error) { $("loginError").textContent = error.message; }
});

$("toggleToken").addEventListener("click", () => {
  const input = $("adminToken");
  input.type = input.type === "password" ? "text" : "password";
  $("toggleToken").textContent = input.type === "password" ? "显示" : "隐藏";
});

$("toggleApiKey").addEventListener("click", () => {
  const input = $("apiKey");
  input.type = input.type === "password" ? "text" : "password";
  $("toggleApiKey").textContent = input.type === "password" ? "显示" : "隐藏";
});

$("toggleVisionApiKey").addEventListener("click", () => {
  const input = $("visionApiKey");
  input.type = input.type === "password" ? "text" : "password";
  $("toggleVisionApiKey").textContent = input.type === "password" ? "显示" : "隐藏";
});

$("toggleFallbackApiKey").addEventListener("click", () => {
  const input = $("fallbackApiKey");
  input.type = input.type === "password" ? "text" : "password";
  $("toggleFallbackApiKey").textContent = input.type === "password" ? "显示" : "隐藏";
});

$("fetchModels").addEventListener("click", async () => {
  const button = $("fetchModels");
  const hint = $("modelFetchHint");
  button.disabled = true;
  button.textContent = "拉取中…";
  hint.textContent = "正在连接模型接口";
  try {
    const result = await api("/admin/api/models", {
      method: "POST",
      body: JSON.stringify({
        api_base_url: $("apiBaseUrl").value.trim(),
        api_key: $("apiKey").value.trim() || null,
      }),
    });
    $("modelOptions").replaceChildren(...result.models.map((id) => {
      const option = document.createElement("option");
      option.value = id;
      return option;
    }));
    hint.textContent = `已拉取 ${result.count} 个模型，可输入或从建议中选择`;
    toast(`已获取 ${result.count} 个模型`);
  } catch (error) {
    hint.textContent = error.message;
  } finally {
    button.disabled = false;
    button.textContent = "拉取模型";
  }
});

$("logoutButton").addEventListener("click", () => { sessionStorage.removeItem(TOKEN_KEY); showLogin(); });
$("refreshButton").addEventListener("click", async () => {
  try { await loadDashboard(); if (location.hash === "#members") await loadMembers(); toast("已刷新运行状态和配置"); } catch (error) { toast(error.message); }
});

$("memberGroupFilter").addEventListener("change", () => loadMembers().catch((error) => toast(error.message)));
$("memberSearch").addEventListener("input", () => {
  clearTimeout(memberSearchTimer);
  memberSearchTimer = setTimeout(() => loadMembers().catch((error) => toast(error.message)), 250);
});
$("refreshMembers").addEventListener("click", () => loadMembers().catch((error) => toast(error.message)));
$("closeMemberDetail").addEventListener("click", () => { selectedMember = null; $("memberDetail").classList.add("hidden"); });
$("memberFavorability").addEventListener("input", () => { $("memberFavorabilityValue").textContent = Number($("memberFavorability").value).toFixed(1); });

$("saveMemberSettings").addEventListener("click", async () => {
  const button = $("saveMemberSettings");
  button.disabled = true;
  $("memberSettingsHint").textContent = "正在保存…";
  try {
    const result = await api("/admin/api/config", { method: "PUT", body: JSON.stringify(collectConfig()) });
    fillConfig(result.config);
    $("memberSettingsHint").textContent = "画像设置已保存并立即生效。";
    toast("画像设置已保存");
  } catch (error) {
    $("memberSettingsHint").textContent = error.message;
  } finally { button.disabled = false; }
});

$("saveHumanizeSettings").addEventListener("click", async () => {
  const button = $("saveHumanizeSettings");
  button.disabled = true;
  $("humanizeSettingsHint").textContent = "正在保存…";
  try {
    const result = await api("/admin/api/config", { method: "PUT", body: JSON.stringify(collectConfig()) });
    fillConfig(result.config);
    $("humanizeSettingsHint").textContent = "拟人设置已保存并立即生效。";
    toast("拟人设置已保存");
  } catch (error) {
    $("humanizeSettingsHint").textContent = error.message;
  } finally { button.disabled = false; }
});

$("webhookToken").addEventListener("input", updateWebhookUrl);

$("genWebhookToken").addEventListener("click", () => {
  const bytes = new Uint8Array(18);
  crypto.getRandomValues(bytes);
  $("webhookToken").value = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
  updateWebhookUrl();
});

$("copyWebhookUrl").addEventListener("click", async () => {
  try { await navigator.clipboard.writeText($("webhookUrl").value); toast("已复制 Webhook 地址"); }
  catch { toast("复制失败，请手动复制"); }
});

$("saveWebhookSettings").addEventListener("click", async () => {
  const button = $("saveWebhookSettings");
  button.disabled = true;
  $("webhookSettingsHint").textContent = "正在保存…";
  $("webhookActionError").textContent = "";
  try {
    const result = await api("/admin/api/config", { method: "PUT", body: JSON.stringify(collectConfig()) });
    fillConfig(result.config);
    $("webhookSettingsHint").textContent = "Webhook 设置已保存并立即生效。";
    toast("Webhook 设置已保存");
  } catch (error) { $("webhookSettingsHint").textContent = error.message; }
  finally { button.disabled = false; }
});

$("testWebhook").addEventListener("click", async () => {
  const token = $("webhookToken").value.trim();
  $("webhookActionError").textContent = "";
  if (!token) { $("webhookActionError").textContent = "请先设置令牌"; return; }
  const button = $("testWebhook");
  button.disabled = true;
  try {
    const response = await fetch(`/webhook/${token}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: "来自管理后台的测试消息" }),
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.detail || `请求失败 (${response.status})`);
    $("webhookSettingsHint").textContent = `测试消息已转发到群 ${body.group_id}`;
    toast("测试消息已发送");
  } catch (error) {
    $("webhookActionError").textContent = `测试失败：${error.message}（改动需先保存后再测试）`;
  } finally { button.disabled = false; }
});

$("toggleJoinGateToken").addEventListener("click", () => {
  const input = $("joinGateToken");
  input.type = input.type === "password" ? "text" : "password";
  $("toggleJoinGateToken").textContent = input.type === "password" ? "显示" : "隐藏";
});

$("saveJoinGate").addEventListener("click", async () => {
  const button = $("saveJoinGate");
  button.disabled = true;
  $("joinGateHint").textContent = "正在保存…";
  $("joinGateError").textContent = "";
  try {
    const result = await api("/admin/api/config", { method: "PUT", body: JSON.stringify(collectConfig()) });
    fillConfig(result.config);
    $("joinGateHint").textContent = "入群设置已保存并立即生效。";
    toast("入群设置已保存");
  } catch (error) { $("joinGateError").textContent = error.message; }
  finally { button.disabled = false; }
});

$("refreshJoinGate").addEventListener("click", async () => {
  const button = $("refreshJoinGate");
  button.disabled = true;
  button.textContent = "刷新中…";
  $("joinGateError").textContent = "";
  try {
    const result = await api("/admin/api/join-gate/refresh", { method: "POST" });
    renderJoinGateStatus(result);
    $("joinGateHint").textContent = `已刷新，名单共 ${result.count} 人。`;
    toast(`名单已刷新：${result.count} 人`);
  } catch (error) { $("joinGateError").textContent = error.message; }
  finally { button.disabled = false; button.textContent = "立即刷新名单"; }
});

$("summaryGroupSelect").addEventListener("change", () => loadSummaryPreview());
$("genSummary").addEventListener("click", () => generateSummary(false));
$("genSendSummary").addEventListener("click", () => generateSummary(true));

$("saveSummary").addEventListener("click", async () => {
  const button = $("saveSummary");
  button.disabled = true;
  $("summaryHint").textContent = "正在保存…";
  $("summaryError").textContent = "";
  try {
    const result = await api("/admin/api/config", { method: "PUT", body: JSON.stringify(collectConfig()) });
    fillConfig(result.config);
    $("summaryHint").textContent = "总结设置已保存并立即生效。";
    toast("总结设置已保存");
  } catch (error) { $("summaryError").textContent = error.message; }
  finally { button.disabled = false; }
});

$("saveMember").addEventListener("click", async () => {
  if (!selectedMember) return;
  const button = $("saveMember");
  button.disabled = true;
  $("memberActionError").textContent = "";
  try {
    const result = await api(`/admin/api/members/${selectedMember.group_id}/${selectedMember.user_id}`, {
      method: "PATCH",
      body: JSON.stringify({ favorability: Number($("memberFavorability").value), admin_note: $("memberAdminNote").value.trim(), bot_nickname: $("memberBotNickname").value.trim(), offense_count: Number($("memberOffenseCount").value) }),
    });
    fillMemberDetail(result.member);
    await loadMembers();
    toast("群员画像已保存");
  } catch (error) { $("memberActionError").textContent = error.message; }
  finally { button.disabled = false; }
});

$("analyzeMember").addEventListener("click", async () => {
  if (!selectedMember) return;
  const button = $("analyzeMember");
  button.disabled = true;
  button.textContent = "分析中…";
  $("memberActionError").textContent = "";
  try {
    const result = await api(`/admin/api/members/${selectedMember.group_id}/${selectedMember.user_id}/analyze`, { method: "POST" });
    fillMemberDetail(result.member);
    await loadMembers();
    toast("画像分析已完成");
  } catch (error) { $("memberActionError").textContent = error.message; }
  finally { button.disabled = false; button.textContent = "立即重新分析"; }
});

$("deleteMember").addEventListener("click", async () => {
  if (!selectedMember || !window.confirm(`确定删除 ${selectedMember.display_name} 的画像和消息记录吗？`)) return;
  const { group_id: groupId, user_id: userId } = selectedMember;
  try {
    await api(`/admin/api/members/${groupId}/${userId}`, { method: "DELETE" });
    selectedMember = null;
    $("memberDetail").classList.add("hidden");
    await loadMembers();
    toast("群员记录已删除");
  } catch (error) { $("memberActionError").textContent = error.message; }
});

$("configForm").addEventListener("input", markDirty);
$("configForm").addEventListener("change", markDirty);
$("configForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("saveError").textContent = "";
  $("saveButton").disabled = true;
  try {
    const payload = collectConfig();
    const result = await api("/admin/api/config", { method: "PUT", body: JSON.stringify(payload) });
    fillConfig(result.config);
    toast("配置已保存并立即生效");
    if (location.hash === "#routine") await loadRoutine();
  } catch (error) {
    $("saveError").textContent = error.message;
    $("saveButton").disabled = false;
  }
});

const initialPage = location.hash.slice(1);
window.addEventListener("hashchange", () => {
  const target = location.hash.slice(1);
  if ([...document.querySelectorAll(".page-section")].some((page) => page.dataset.page === target)) switchPage(target);
});
if (["overview", "requests", "access", "conversation", "prompts", "members", "humanize", "routine", "moderation", "jev", "intelligence", "model", "search", "voice", "webhook", "joingate", "summary"].includes(initialPage)) switchPage(initialPage);
if (token()) loadDashboard().catch(() => showLogin()); else showLogin();
