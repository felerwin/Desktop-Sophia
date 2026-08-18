SOPHIA INSIGHT 0.2.0 - CHROMIECRAFT / WOW 3.3.5a
=================================================

PURPOSE
-------
This is a read-only telemetry addon for Desktop Sophia. It observes ordinary
WoW addon events and maintains a compact snapshot plus a 250-event journal in:

  WTF\Account\<ACCOUNT>\SavedVariables\SophiaInsight.lua

It observes player/target state, zones and instances, combat boundaries,
deaths, kills, interrupts, resurrection, loot messages, quests, achievements,
level-ups, group size, health, power, and activity state.

INSTALL
-------
Copy the SophiaInsight folder into:

  <ChromieCraft>\Interface\AddOns\SophiaInsight\

At the character screen, open AddOns and enable Sophia Insight. If necessary,
enable "Load out of date AddOns"; the addon declares Interface 30300 for the
3.3.5a client.

COMMANDS
--------
  /sophia
  /sophia snapshot
  /sophia clear
  /sophia combatlog on
  /sophia combatlog off
  /sophiabridge on
  /sophiabridge off

LIVE PIXEL BRIDGE
-----------------
Version 0.2 renders a small 12x12 color grid in the upper-left corner. Desktop
Sophia decodes this visible, checksummed signal four times per second. It sends
exact health/power/target state plus zone, loot, and all 19 equipment slots.
The grid must remain visible for the decoder to work. It never reads process
memory and never sends input to the game.

THE DESKTOP BRIDGE
------------------
Combat-file logging is enabled by default. WoW writes its supported combat
feed to Logs\WoWCombatLog.txt. The next desktop-side phase will tail that file
and distill meaningful events into Sophia's prompt context. Use
/sophia combatlog off if you do not want the file written.

LIMITS AND SAFETY
-----------------
WoW 3.3.5 addons cannot open arbitrary local sockets or continuously flush
SavedVariables to disk. SavedVariables are written when the UI reloads or the
client logs out. The combat log is the supported live bridge. This addon does
not read process memory, send gameplay input, automate actions, or communicate
with the ChromieCraft server beyond normal client behavior.
