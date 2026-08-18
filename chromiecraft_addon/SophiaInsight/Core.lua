local ADDON_NAME = ...
local frame = CreateFrame("Frame", "SophiaInsightFrame")
local playerGUID = nil
local lastTargetGUID = nil
local lastSnapshotAt = 0
local MAX_EVENTS = 250

local function now()
    return date("!%Y-%m-%dT%H:%M:%SZ")
end

local function clean(value, limit)
    value = tostring(value or "")
    value = string.gsub(value, "[\r\n\t]", " ")
    if limit and string.len(value) > limit then
        value = string.sub(value, 1, limit)
    end
    return value
end

local function ensureDB()
    if type(SophiaInsightDB) ~= "table" then SophiaInsightDB = {} end
    if type(SophiaInsightDB.settings) ~= "table" then SophiaInsightDB.settings = {} end
    if SophiaInsightDB.settings.combatLog == nil then
        SophiaInsightDB.settings.combatLog = true
    end
    if type(SophiaInsightDB.snapshot) ~= "table" then SophiaInsightDB.snapshot = {} end
    if type(SophiaInsightDB.events) ~= "table" then SophiaInsightDB.events = {} end
    SophiaInsightDB.version = "0.2.0"
end

local function addEvent(kind, data)
    ensureDB()
    local event = {
        time = now(),
        kind = clean(kind, 40),
        data = data or {},
    }
    table.insert(SophiaInsightDB.events, event)
    while #SophiaInsightDB.events > MAX_EVENTS do
        table.remove(SophiaInsightDB.events, 1)
    end
    SophiaInsightDB.lastEvent = event
end

local function percent(current, maximum)
    current = tonumber(current) or 0
    maximum = tonumber(maximum) or 0
    if maximum <= 0 then return 0 end
    return math.floor((current / maximum) * 100 + 0.5)
end

local function unitState(unit)
    if not UnitExists(unit) then return nil end
    return {
        guid = UnitGUID(unit),
        name = clean(UnitName(unit), 80),
        level = UnitLevel(unit) or 0,
        class = select(2, UnitClass(unit)),
        classification = UnitClassification(unit),
        reaction = UnitReaction("player", unit),
        healthPercent = percent(UnitHealth(unit), UnitHealthMax(unit)),
        powerPercent = percent(UnitPower(unit), UnitPowerMax(unit)),
        dead = UnitIsDeadOrGhost(unit) and true or false,
        player = UnitIsPlayer(unit) and true or false,
    }
end

local function questSummary()
    local entries, quests = GetNumQuestLogEntries()
    entries = entries or 0
    quests = quests or 0
    local complete = 0
    for index = 1, entries do
        local _, _, _, _, _, isComplete, _, questID = GetQuestLogTitle(index)
        if questID and isComplete == 1 then complete = complete + 1 end
    end
    return { entries = entries, quests = quests, complete = complete }
end

local function updateSnapshot(reason)
    ensureDB()
    local zone = GetRealZoneText() or GetZoneText() or ""
    local subzone = GetSubZoneText() or ""
    local instanceName, instanceType, difficultyID, difficultyName,
        maxPlayers, dynamicDifficulty, isDynamic = GetInstanceInfo()
    local inInstance = IsInInstance()
    local target = unitState("target")

    SophiaInsightDB.snapshot = {
        capturedAt = now(),
        reason = clean(reason, 40),
        player = unitState("player"),
        target = target,
        focus = unitState("focus"),
        zone = clean(zone, 100),
        subzone = clean(subzone, 100),
        instance = {
            active = inInstance and true or false,
            name = clean(instanceName, 100),
            type = clean(instanceType, 30),
            difficulty = clean(difficultyName, 60),
            difficultyID = difficultyID or 0,
            maxPlayers = maxPlayers or 0,
            dynamic = isDynamic and true or false,
        },
        combat = UnitAffectingCombat("player") and true or false,
        resting = IsResting() and true or false,
        mounted = IsMounted() and true or false,
        groupSize = GetNumRaidMembers() > 0 and GetNumRaidMembers() or GetNumPartyMembers() + 1,
        quests = questSummary(),
        money = GetMoney() or 0,
    }

    local targetGUID = target and target.guid or nil
    if targetGUID ~= lastTargetGUID then
        lastTargetGUID = targetGUID
        if target then
            addEvent("target_changed", {
                name = target.name,
                level = target.level,
                classification = target.classification,
                hostile = UnitCanAttack("player", "target") and true or false,
            })
        else
            addEvent("target_cleared", {})
        end
    end
end

local function setCombatLogging(enabled)
    ensureDB()
    SophiaInsightDB.settings.combatLog = enabled and true or false
    LoggingCombat(enabled and true or false)
    addEvent("combat_log", { enabled = enabled and true or false })
    DEFAULT_CHAT_FRAME:AddMessage(
        "|cffc29beaSophia Insight:|r combat-file bridge " .. (enabled and "enabled" or "disabled") .. "."
    )
end

local function statusText()
    ensureDB()
    local snapshot = SophiaInsightDB.snapshot or {}
    local target = snapshot.target and snapshot.target.name or "none"
    return string.format(
        "zone=%s | combat=%s | target=%s | events=%d | combat log=%s",
        clean(snapshot.zone or "unknown", 40),
        snapshot.combat and "yes" or "no",
        clean(target, 40),
        #SophiaInsightDB.events,
        SophiaInsightDB.settings.combatLog and "on" or "off"
    )
end

local function onCombatLog(...)
    local timestamp, subEvent, sourceGUID, sourceName, sourceFlags,
        destGUID, destName, destFlags = ...
    if not subEvent then return end

    if subEvent == "PARTY_KILL" and sourceGUID == playerGUID then
        addEvent("kill", { target = clean(destName, 100), guid = destGUID })
    elseif subEvent == "UNIT_DIED" then
        if destGUID == playerGUID then
            addEvent("player_died", { source = clean(sourceName, 100) })
        elseif destGUID == lastTargetGUID then
            addEvent("target_died", { target = clean(destName, 100), guid = destGUID })
        end
    elseif subEvent == "SPELL_INTERRUPT" and sourceGUID == playerGUID then
        local spellID, spellName, spellSchool, extraSpellID, extraSpellName = select(9, ...)
        addEvent("interrupt", {
            target = clean(destName, 100),
            spell = clean(spellName, 100),
            interrupted = clean(extraSpellName, 100),
        })
    elseif subEvent == "SPELL_RESURRECT" and sourceGUID == playerGUID then
        local spellID, spellName = select(9, ...)
        addEvent("resurrect", { target = clean(destName, 100), spell = clean(spellName, 100) })
    end
end

local function onEvent(self, event, ...)
    if event == "ADDON_LOADED" then
        local loaded = ...
        if loaded ~= ADDON_NAME then return end
        ensureDB()
        return
    end

    if event == "PLAYER_LOGIN" then
        ensureDB()
        playerGUID = UnitGUID("player")
        if SophiaInsightDB.settings.combatLog then LoggingCombat(true) end
        updateSnapshot("login")
        addEvent("session_started", {
            player = clean(UnitName("player"), 80),
            realm = clean(GetRealmName(), 80),
        })
        DEFAULT_CHAT_FRAME:AddMessage("|cffc29beaSophia Insight 0.2:|r eyes open. Type |cffffffff/sophia|r for status.")
    elseif event == "PLAYER_ENTERING_WORLD" then
        playerGUID = UnitGUID("player")
        updateSnapshot("entering_world")
        addEvent("entered_world", {
            zone = clean(GetRealZoneText(), 100),
            instance = IsInInstance() and true or false,
        })
    elseif event == "PLAYER_LOGOUT" then
        updateSnapshot("logout")
        addEvent("session_ended", {})
    elseif event == "COMBAT_LOG_EVENT_UNFILTERED" then
        onCombatLog(...)
    elseif event == "PLAYER_REGEN_DISABLED" then
        updateSnapshot("combat_started")
        addEvent("combat_started", { target = clean(UnitName("target"), 100) })
    elseif event == "PLAYER_REGEN_ENABLED" then
        updateSnapshot("combat_ended")
        addEvent("combat_ended", {})
    elseif event == "PLAYER_DEAD" then
        updateSnapshot("player_dead")
        addEvent("player_dead", { zone = clean(GetRealZoneText(), 100) })
    elseif event == "PLAYER_ALIVE" or event == "PLAYER_UNGHOST" then
        updateSnapshot("player_alive")
        addEvent("player_alive", {})
    elseif event == "ZONE_CHANGED_NEW_AREA" or event == "ZONE_CHANGED" or event == "ZONE_CHANGED_INDOORS" then
        updateSnapshot("zone_changed")
        addEvent("zone_changed", {
            zone = clean(GetRealZoneText(), 100),
            subzone = clean(GetSubZoneText(), 100),
        })
    elseif event == "PLAYER_TARGET_CHANGED" then
        updateSnapshot("target_changed")
    elseif event == "QUEST_ACCEPTED" then
        local index, questID = ...
        local title = index and GetQuestLogTitle(index) or ""
        updateSnapshot("quest_accepted")
        addEvent("quest_accepted", { id = questID, title = clean(title, 140) })
    elseif event == "QUEST_COMPLETE" then
        addEvent("quest_ready", { title = clean(GetTitleText(), 140) })
    elseif event == "QUEST_FINISHED" then
        updateSnapshot("quest_finished")
        addEvent("quest_finished", {})
    elseif event == "ACHIEVEMENT_EARNED" then
        local achievementID = ...
        local _, name = GetAchievementInfo(achievementID)
        addEvent("achievement", { id = achievementID, title = clean(name, 140) })
    elseif event == "CHAT_MSG_LOOT" then
        local message = ...
        addEvent("loot", { text = clean(message, 220) })
    elseif event == "PLAYER_LEVEL_UP" then
        local level = ...
        updateSnapshot("level_up")
        addEvent("level_up", { level = level })
    elseif event == "PLAYER_XP_UPDATE" or event == "UPDATE_INSTANCE_INFO" then
        updateSnapshot(string.lower(event))
    end
end

frame:SetScript("OnEvent", onEvent)
frame:SetScript("OnUpdate", function(self, elapsed)
    lastSnapshotAt = lastSnapshotAt + elapsed
    if lastSnapshotAt >= 1 then
        lastSnapshotAt = 0
        updateSnapshot("heartbeat")
    end
end)

local events = {
    "ADDON_LOADED", "PLAYER_LOGIN", "PLAYER_LOGOUT", "PLAYER_ENTERING_WORLD",
    "COMBAT_LOG_EVENT_UNFILTERED", "PLAYER_REGEN_DISABLED", "PLAYER_REGEN_ENABLED",
    "PLAYER_DEAD", "PLAYER_ALIVE", "PLAYER_UNGHOST", "PLAYER_TARGET_CHANGED",
    "ZONE_CHANGED_NEW_AREA", "ZONE_CHANGED", "ZONE_CHANGED_INDOORS",
    "QUEST_ACCEPTED", "QUEST_COMPLETE", "QUEST_FINISHED", "ACHIEVEMENT_EARNED",
    "CHAT_MSG_LOOT", "PLAYER_LEVEL_UP", "PLAYER_XP_UPDATE", "UPDATE_INSTANCE_INFO",
}
for _, event in ipairs(events) do frame:RegisterEvent(event) end

SLASH_SOPHIAINSIGHT1 = "/sophia"
SlashCmdList.SOPHIAINSIGHT = function(message)
    message = string.lower(clean(message, 80))
    if message == "combatlog on" then
        setCombatLogging(true)
    elseif message == "combatlog off" then
        setCombatLogging(false)
    elseif message == "clear" then
        SophiaInsightDB.events = {}
        DEFAULT_CHAT_FRAME:AddMessage("|cffc29beaSophia Insight:|r event journal cleared.")
    elseif message == "snapshot" then
        updateSnapshot("manual")
        DEFAULT_CHAT_FRAME:AddMessage("|cffc29beaSophia Insight:|r snapshot refreshed.")
    else
        updateSnapshot("status")
        DEFAULT_CHAT_FRAME:AddMessage("|cffc29beaSophia Insight:|r " .. statusText())
        DEFAULT_CHAT_FRAME:AddMessage("Commands: /sophia snapshot, /sophia clear, /sophia combatlog on|off, /sophiabridge on|off")
    end
end
