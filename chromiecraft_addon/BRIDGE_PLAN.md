# Sophia ↔ ChromieCraft bridge

The safe integration has two inputs:

1. `Logs/WoWCombatLog.txt` for low-latency combat events while the game runs.
2. `WTF/Account/<ACCOUNT>/SavedVariables/SophiaInsight.lua` for richer state
   after a logout or `/reload` flush.

The desktop bridge will tail only appended combat-log lines, reduce them to a
small rolling context, and expose events such as encounter start/end, player
death, target death, interrupt, healing pressure, and significant loot. It will
never inject keyboard/mouse input or inspect WoW process memory.

The next implementation step needs the local ChromieCraft installation path.
