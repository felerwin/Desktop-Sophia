import csv
import queue
import re
import threading
import time
from collections import deque
from pathlib import Path

from wow_pixel_bridge import WowPixelBridge


class GameEventEngine:
    """Tails local game logs and emits normalized semantic events for Ember."""
    REACTION_TYPES = {"boss_start", "boss_victory", "boss_wipe", "player_death", "level_up", "quest_complete", "critical_health", "valuable_loot"}

    def __init__(self, root, config, on_event=None):
        self.root = Path(root); self.config = config; self.on_event = on_event
        self.events = deque(maxlen=80); self.reaction_queue = queue.Queue(maxsize=30)
        self.stop_event = threading.Event(); self.thread = None; self.log_path = None
        self.status = "searching"; self.last_error = None; self._recent_signatures = {}
        self.active_fight = None; self.live_state = {}; self._known_gear_ids = {}; self._last_low_health_at = 0
        self.pixel_bridge = WowPixelBridge(config, self._handle_pixel_packet)

    def _candidate_paths(self):
        configured = str(self.config.get("wow_combat_log_path") or "").strip()
        if configured: yield Path(configured)
        bases = [Path("C:/Program Files (x86)/World of Warcraft"), Path("C:/Program Files/World of Warcraft"), Path.home()/"Desktop"/"ChromieCraft_3.3.5a", Path.home()/"Desktop"/"world of warcraft 3.3.5a hd"]
        for base in bases:
            for flavor in ["_classic_", "_classic_era_", "_retail_", "_ptr_"]:
                yield base/flavor/"Logs"/"WoWCombatLog.txt"

    def discover(self):
        for candidate in self._candidate_paths():
            try:
                if candidate.is_file(): self.log_path = candidate; return candidate
            except OSError: pass
        return None

    def start(self):
        if self.thread and self.thread.is_alive(): return
        self.stop_event.clear(); self.thread = threading.Thread(target=self._run, name="ember-game-events", daemon=True); self.thread.start()
        if self.config.get("wow_pixel_bridge", True): self.pixel_bridge.start()

    def stop(self):
        self.stop_event.set()
        if self.thread: self.thread.join(timeout=2)
        self.pixel_bridge.stop()

    def snapshot(self):
        return {"status":self.status,"log_path":str(self.log_path) if self.log_path else None,"configured_path":str(self.config.get("wow_combat_log_path") or ""),"player_name":str(self.config.get("wow_player_name") or ""),"last_error":self.last_error,"recent":list(self.events)[:30],"active_fight":self._fight_context(),"telemetry":self.pixel_bridge.snapshot()}

    def context(self):
        return {"status":self.status,"player":str(self.config.get("wow_player_name") or ""),"active_fight":self._fight_context(),"live_state":dict(self.live_state),"equipped_gear":[{k:i.get(k) for k in ("slot","item_id","quality","item_level","name")} for i in self.pixel_bridge.snapshot().get("gear",[]) if i.get("item_id")],"recent_events":list(self.events)[:8]}

    def semantic_context(self):
        """Compact context intended for Ember's WorldState, not direct prompt dumping."""
        return {"game":"World of Warcraft","live_state":dict(self.live_state),"recent_events":list(self.events)[:12],"active_fight":self._fight_context()}

    def _handle_pixel_packet(self, packet):
        kind=packet.get("kind")
        if kind=="state":
            prev=self.live_state.get("health",100); self.live_state.update(packet); health=int(packet.get("health",100)); now=time.time()
            if packet.get("combat") and health<=25 and prev>25 and now-self._last_low_health_at>=45:
                self._last_low_health_at=now; self.inject("critical_health",f"Health dropped to {health}%",{"health_percent":health,"target":self.live_state.get("target_name")},"wow_pixel_bridge")
        elif kind=="gear":
            slot=int(packet.get("slot",0)); item=int(packet.get("item_id",0)); prev=self._known_gear_ids.get(slot); self._known_gear_ids[slot]=item
            if prev is not None and prev!=item: self.inject("gear_change",f"Equipment changed: {packet.get('name') or 'empty slot'}",{k:packet.get(k) for k in ("slot","item_id","quality","item_level","name")},"wow_pixel_bridge")
        elif kind=="loot":
            typ="valuable_loot" if int(packet.get("quality",0))>=3 else "loot_pickup"; self.inject(typ,f"Looted {packet.get('name') or 'an item'}",{k:packet.get(k) for k in ("item_id","count","quality","item_level","name")},"wow_pixel_bridge")
        elif kind=="zone":
            prev=self.live_state.get("zone"); self.live_state.update(packet)
            if prev and packet.get("zone")!=prev: self.inject("zone_change",f"Entered {packet.get('zone')}",dict(packet),"wow_pixel_bridge")
        elif kind=="target":
            self.live_state.update({"target_name":packet.get("name"),"target_level":packet.get("level"),"target_classification":packet.get("classification")})
            if int(packet.get("classification",0))==4 and packet.get("name"): self.inject("boss_start",f"Targeted world boss: {packet.get('name')}",dict(packet),"wow_pixel_bridge")

    def _fight_context(self):
        if not self.active_fight:return None
        f=self.active_fight; return {"seconds":round(time.time()-f["started_at"],1),"targets":sorted(f["targets"])[:6],"damage_done":f["damage_done"],"damage_taken":f["damage_taken"],"kills":f["kills"]}
    def _touch_fight(self,target=None):
        now=time.time()
        if self.active_fight is None:self.active_fight={"started_at":now,"last_at":now,"targets":set(),"damage_done":0,"damage_taken":0,"kills":0}
        self.active_fight["last_at"]=now
        if target:self.active_fight["targets"].add(target)
    def _finish_fight_if_idle(self,force=False):
        f=self.active_fight
        if not f or (not force and time.time()-f["last_at"]<6):return
        duration=max(.1,f["last_at"]-f["started_at"]); targets=sorted(f["targets"]); self.active_fight=None
        self.inject("combat_summary",f"Fight ended: {', '.join(targets[:3]) or 'unknown target'}",{"duration_seconds":round(duration,1),"targets":targets[:8],"damage_done":f["damage_done"],"damage_taken":f["damage_taken"],"kills":f["kills"]},"wow_combat_log")
    def pop_reaction(self):
        try:return self.reaction_queue.get_nowait()
        except queue.Empty:return None
    def inject(self,event_type,title=None,details=None,source="dashboard"):
        labels={"boss_start":"Boss encounter started","boss_victory":"Boss defeated","boss_wipe":"Boss attempt failed","player_death":"Player died","level_up":"Level gained","quest_complete":"Quest completed","zone_change":"Zone changed"}
        event={"event_type":event_type,"game":"World of Warcraft","title":title or labels.get(event_type,event_type.replace("_"," ").title()),"details":details or {},"priority":"high" if event_type in self.REACTION_TYPES else "normal","source":source,"time":time.strftime("%H:%M:%S")}; self._emit(event); return event
    def _emit(self,event):
        sig=f"{event['event_type']}:{event['title']}"; now=time.time()
        if now-self._recent_signatures.get(sig,0)<12:return
        self._recent_signatures[sig]=now; self.events.appendleft(event)
        if event["event_type"] in self.REACTION_TYPES:
            try:self.reaction_queue.put_nowait(event)
            except queue.Full:pass
        if self.on_event:self.on_event(event)

    def parse_line(self,line):
        m=re.search(r"\s{2}([A-Z][A-Z_]+),",line)
        if not m:return None
        payload=line[m.start(1):]
        try:parts=[p.strip().strip('"') for p in next(csv.reader([payload]))]
        except Exception:parts=[p.strip().strip('"') for p in payload.split(",")]
        kind=parts[0] if parts else None
        if not kind:return None
        if kind=="ENCOUNTER_START":
            name=parts[2] if len(parts)>2 else "Boss"; return self.inject("boss_start",f"Encounter started: {name}",{"encounter":name},"wow_combat_log")
        if kind=="ENCOUNTER_END":
            name=parts[2] if len(parts)>2 else "Boss"; success=parts[-1]=="1" if parts else False; return self.inject("boss_victory" if success else "boss_wipe",f"{name} defeated" if success else f"Attempt on {name} ended",{"encounter":name,"success":success},"wow_combat_log")
        configured=str(self.config.get("wow_player_name") or "").strip().lower(); source=parts[2] if len(parts)>2 else ""; dest=parts[5] if len(parts)>5 else ""; player_source=configured and source.lower()==configured; player_dest=configured and dest.lower()==configured
        if kind=="UNIT_DIED" and configured:
            name=next((p for p in parts[1:] if configured in p.lower()),None)
            if name:self._finish_fight_if_idle(force=True); return self.inject("player_death",f"{name} died",{"name":name},"wow_combat_log")
        if kind=="PARTY_KILL" and player_source:self._touch_fight(dest); self.active_fight["kills"]+=1; return self.inject("enemy_kill",f"Defeated {dest}",{"target":dest},"wow_combat_log")
        if kind.endswith("_DAMAGE") and (player_source or player_dest):
            idx=7 if kind=="SWING_DAMAGE" else 10
            try:amount=int(float(parts[idx]))
            except (IndexError,TypeError,ValueError):amount=0
            target=dest if player_source else source; self._touch_fight(target)
            if player_source:self.active_fight["damage_done"]+=amount
            if player_dest:self.active_fight["damage_taken"]+=amount
        if kind=="SPELL_INTERRUPT" and player_source:
            spell=parts[11] if len(parts)>11 else "spell"; self._touch_fight(dest); return self.inject("interrupt",f"Interrupted {spell}",{"target":dest,"spell":spell},"wow_combat_log")
        if kind=="PLAYER_LEVEL_UP":
            level=parts[1] if len(parts)>1 else ""; return self.inject("level_up",f"Level {level} reached".strip(),{"level":level},"wow_log")
        if kind=="QUEST_TURNED_IN":
            quest=parts[2] if len(parts)>2 else "Quest"; return self.inject("quest_complete",f"Quest completed: {quest}",{"quest":quest},"wow_log")
        return None

    def _run(self):
        while not self.stop_event.is_set():
            path=self.discover()
            if path is None:self.status="waiting_for_log"; self.stop_event.wait(10); continue
            try:
                self.status="watching"
                with path.open("r",encoding="utf-8",errors="replace") as handle:
                    handle.seek(0,2)
                    while not self.stop_event.is_set():
                        line=handle.readline()
                        if not line:self._finish_fight_if_idle(); self.stop_event.wait(.5); continue
                        self.parse_line(line)
            except Exception as exc:self.status="error"; self.last_error=str(exc)[:300]; self.stop_event.wait(5)
