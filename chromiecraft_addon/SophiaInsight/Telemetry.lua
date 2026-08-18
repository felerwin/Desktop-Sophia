-- Sophia Insight pixel bridge v1. One-way, read-only telemetry rendered into
-- a 12x12 color grid for Desktop Sophia to decode from ordinary screenshots.

local GRID = 12
local CELL = 8
local MARKER = { 2, 3, 4, 5, 6, 7, 1, 15 }
local palette = {
    {0,0,0}, {1,1,1}, {1,0,0}, {0,1,0}, {0,0,1}, {1,1,0}, {0,1,1}, {1,0,1},
    {1,.5,0}, {.5,0,1}, {.5,1,0}, {1,.25,.5}, {0,.5,.5}, {0,0,.5}, {.5,.5,.5}, {.5,.25,0},
}
local bridge = CreateFrame("Frame", "SophiaInsightPixelBridge", UIParent)
local cells = {}
local eventQueue = {}
local sequence = 0
local elapsedSincePacket = 0
local lastWasEvent = false
local lastTargetGUID = nil
local bridgeReady = false

local function byte(value)
    value = math.floor(tonumber(value) or 0)
    if value < 0 then value = 0 end
    if value > 255 then value = 255 end
    return string.char(value)
end

local function u16(value)
    value = math.max(0, math.floor(tonumber(value) or 0))
    return string.char(mod(value, 256), mod(math.floor(value / 256), 256))
end

local function u32(value)
    value = math.max(0, math.floor(tonumber(value) or 0))
    return string.char(
        mod(value, 256), mod(math.floor(value / 256), 256),
        mod(math.floor(value / 65536), 256), mod(math.floor(value / 16777216), 256)
    )
end

local function clampText(value, maximum)
    value = tostring(value or "")
    value = string.gsub(value, "[\r\n\t]", " ")
    if string.len(value) > maximum then value = string.sub(value, 1, maximum) end
    return value
end

local function percent(current, maximum)
    current = tonumber(current) or 0
    maximum = tonumber(maximum) or 0
    if maximum <= 0 then return 0 end
    return math.max(0, math.min(100, math.floor(current * 100 / maximum + .5)))
end

local function classificationCode(unit)
    local value = UnitExists(unit) and UnitClassification(unit) or "normal"
    if value == "worldboss" then return 4 end
    if value == "rareelite" then return 3 end
    if value == "elite" then return 2 end
    if value == "rare" then return 1 end
    return 0
end

local function itemData(link)
    if not link then return 0, 0, 0, "" end
    local itemID = tonumber(string.match(link, "item:(%d+)")) or 0
    local name, _, quality, itemLevel = GetItemInfo(link)
    name = name or string.match(link, "%[(.-)%]") or ("Item " .. itemID)
    return itemID, quality or 0, itemLevel or 0, clampText(name, 52)
end

local function queuePacket(packetType, payload)
    if #eventQueue >= 60 then table.remove(eventQueue, 1) end
    table.insert(eventQueue, { packetType, payload or "" })
end

local function queueGear(slot)
    slot = tonumber(slot) or 0
    if slot < 1 or slot > 19 then return end
    local link = GetInventoryItemLink("player", slot)
    local itemID, quality, itemLevel, name = itemData(link)
    queuePacket(2, byte(slot) .. u32(itemID) .. byte(quality) .. u16(itemLevel) .. name)
end

local function queueAllGear()
    for slot = 1, 19 do queueGear(slot) end
end

local function queueZone()
    local zone = GetRealZoneText() or GetZoneText() or ""
    local subzone = GetSubZoneText() or ""
    queuePacket(4, clampText(zone .. "|" .. subzone, 62))
end

local function queueTarget()
    if not UnitExists("target") then
        queuePacket(5, byte(0) .. byte(0) .. "")
        return
    end
    local level = UnitLevel("target") or 0
    if level < 0 then level = 255 end
    queuePacket(5, byte(level) .. byte(classificationCode("target")) .. clampText(UnitName("target"), 60))
end

local function statePayload()
    local flags = 0
    if UnitAffectingCombat("player") then flags = flags + 1 end
    if IsResting() then flags = flags + 2 end
    if IsMounted() then flags = flags + 4 end
    if UnitIsDeadOrGhost("player") then flags = flags + 8 end
    if UnitExists("target") then flags = flags + 16 end
    if UnitExists("target") and UnitCanAttack("player", "target") then flags = flags + 32 end

    local targetHealth = 0
    local targetLevel = 0
    if UnitExists("target") then
        targetHealth = percent(UnitHealth("target"), UnitHealthMax("target"))
        targetLevel = UnitLevel("target") or 0
        if targetLevel < 0 then targetLevel = 255 end
    end
    local threat = UnitThreatSituation and (UnitThreatSituation("player", "target") or 0) or 0
    local groupSize = GetNumRaidMembers() > 0 and GetNumRaidMembers() or GetNumPartyMembers() + 1
    return byte(percent(UnitHealth("player"), UnitHealthMax("player")))
        .. byte(percent(UnitPower("player"), UnitPowerMax("player")))
        .. byte(targetHealth) .. byte(flags) .. byte(UnitLevel("player") or 0)
        .. byte(targetLevel) .. byte(classificationCode("target"))
        .. byte(threat) .. byte(groupSize)
end

local function renderPacket(packetType, payload)
    sequence = mod(sequence + 1, 256)
    if string.len(payload) > 63 then payload = string.sub(payload, 1, 63) end
    local raw = byte(1) .. byte(sequence) .. byte(packetType) .. byte(string.len(payload)) .. payload
    local checksum = 0
    for index = 1, string.len(raw) do checksum = mod(checksum + string.byte(raw, index), 256) end
    raw = raw .. byte(checksum)

    local symbols = {}
    for _, value in ipairs(MARKER) do table.insert(symbols, value) end
    for index = 1, string.len(raw) do
        local value = string.byte(raw, index)
        table.insert(symbols, math.floor(value / 16))
        table.insert(symbols, mod(value, 16))
    end
    while #symbols < GRID * GRID do table.insert(symbols, 0) end

    for index = 1, GRID * GRID do
        local color = palette[(symbols[index] or 0) + 1]
        cells[index]:SetTexture(color[1], color[2], color[3], 1)
    end
end

local function configureBridge()
    if bridgeReady then return end
    bridgeReady = true
    bridge:SetFrameStrata("TOOLTIP")
    bridge:SetClampedToScreen(true)
    bridge:EnableMouse(false)
    bridge:SetWidth(GRID * CELL)
    bridge:SetHeight(GRID * CELL)
    bridge:ClearAllPoints()
    bridge:SetPoint("TOPLEFT", UIParent, "TOPLEFT", 2, -2)
    local effective = UIParent:GetEffectiveScale() or 1
    if effective > 0 then bridge:SetScale(1 / effective) end
    for row = 0, GRID - 1 do
        for column = 0, GRID - 1 do
            local texture = bridge:CreateTexture(nil, "OVERLAY")
            texture:SetWidth(CELL)
            texture:SetHeight(CELL)
            texture:SetPoint("TOPLEFT", bridge, "TOPLEFT", column * CELL, -row * CELL)
            cells[row * GRID + column + 1] = texture
        end
    end
    bridge:Show()
end

local function bridgeEnabled()
    return not SophiaInsightDB or not SophiaInsightDB.settings
        or SophiaInsightDB.settings.pixelBridge ~= false
end

bridge:SetScript("OnUpdate", function(self, elapsed)
    if not bridgeReady then return end
    if not bridgeEnabled() then self:Hide(); return end
    if not self:IsShown() then self:Show() end
    elapsedSincePacket = elapsedSincePacket + elapsed
    if elapsedSincePacket < .25 then return end
    elapsedSincePacket = 0
    if #eventQueue > 0 and not lastWasEvent then
        local packet = table.remove(eventQueue, 1)
        renderPacket(packet[1], packet[2])
        lastWasEvent = true
    else
        renderPacket(1, statePayload())
        lastWasEvent = false
    end
end)

bridge:RegisterEvent("PLAYER_LOGIN")
bridge:RegisterEvent("PLAYER_ENTERING_WORLD")
bridge:RegisterEvent("PLAYER_TARGET_CHANGED")
bridge:RegisterEvent("ZONE_CHANGED_NEW_AREA")
bridge:RegisterEvent("ZONE_CHANGED")
bridge:RegisterEvent("ZONE_CHANGED_INDOORS")
bridge:RegisterEvent("PLAYER_EQUIPMENT_CHANGED")
bridge:RegisterEvent("CHAT_MSG_LOOT")
bridge:SetScript("OnEvent", function(self, event, ...)
    if event == "PLAYER_LOGIN" then
        if SophiaInsightDB and SophiaInsightDB.settings and SophiaInsightDB.settings.pixelBridge == nil then
            SophiaInsightDB.settings.pixelBridge = true
        end
        configureBridge()
        queueAllGear()
        queueZone()
        queueTarget()
    elseif event == "PLAYER_ENTERING_WORLD" then
        configureBridge()
        queueAllGear()
        queueZone()
    elseif event == "PLAYER_TARGET_CHANGED" then
        local guid = UnitGUID("target")
        if guid ~= lastTargetGUID then
            lastTargetGUID = guid
            queueTarget()
        end
    elseif event == "ZONE_CHANGED_NEW_AREA" or event == "ZONE_CHANGED" or event == "ZONE_CHANGED_INDOORS" then
        queueZone()
    elseif event == "PLAYER_EQUIPMENT_CHANGED" then
        local slot = ...
        queueGear(slot)
    elseif event == "CHAT_MSG_LOOT" then
        local message = ...
        local link = message and string.match(message, "(|c%x+|Hitem:.-|h%[.-%]|h|r)")
        if link then
            local itemID, quality, itemLevel, name = itemData(link)
            local count = tonumber(string.match(message, "x(%d+)")) or 1
            queuePacket(3, u32(itemID) .. byte(count) .. byte(quality) .. u16(itemLevel) .. name)
        end
    end
end)

SLASH_SOPHIABRIDGE1 = "/sophiabridge"
SlashCmdList.SOPHIABRIDGE = function(message)
    message = string.lower(tostring(message or ""))
    SophiaInsightDB.settings.pixelBridge = message ~= "off"
    if SophiaInsightDB.settings.pixelBridge then
        configureBridge()
        bridge:Show()
        queueAllGear()
        DEFAULT_CHAT_FRAME:AddMessage("|cffc29beaSophia Insight:|r live pixel bridge enabled.")
    else
        bridge:Hide()
        DEFAULT_CHAT_FRAME:AddMessage("|cffc29beaSophia Insight:|r live pixel bridge disabled.")
    end
end
