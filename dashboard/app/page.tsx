export default function Home() {
  return (
    <main className="shell">
      <header className="topbar">
        <div className="identity">
          <div className="avatar" aria-hidden="true"><span /></div>
          <div>
            <p className="eyebrow">DESKTOP COMPANION</p>
            <h1>Ember</h1>
          </div>
        </div>
        <div className="headerActions">
          <div className="statusPill"><i /> Awake · Listening</div>
          <button className="sleepButton" type="button">Put her to sleep</button>
        </div>
      </header>

      <section className="pulseCard" aria-label="Current status">
        <div className="pulseCopy">
          <p className="eyebrow">RIGHT NOW</p>
          <h2>I’m listening.</h2>
          <p>H510-PRO Wireless · Lily · Terra + Luna</p>
        </div>
        <div className="wave" aria-hidden="true">
          {[18, 32, 48, 25, 55, 38, 61, 29, 44, 20, 36, 16].map((height, i) => (
            <span key={i} style={{ height }} />
          ))}
        </div>
        <div className="pulseMeta">
          <span>Mic live</span>
          <strong>00:18:42</strong>
        </div>
      </section>

      <section className="metricGrid" aria-label="Session metrics">
        <article><span>First audio</span><strong>2.94s</strong><small>↓ 0.98s today</small></article>
        <article><span>Model first text</span><strong>0.62s</strong><small>Terra + Luna · hybrid</small></article>
        <article><span>Speech endpoint</span><strong>1.00s</strong><small>Stable</small></article>
        <article><span>Session cost</span><strong>$0.0118</strong><small>5 calls</small></article>
      </section>

      <section className="panel mediaBay" id="the-tube">
        <div className="panelHeader"><div><p className="eyebrow">THE TUBE</p><h3>YouTube player</h3></div><small>Ready for a video</small></div>
        <div className="mediaLayout">
          <div>
            <div className="youtubeStage" aria-label="YouTube player preview" />
            <div className="youtubeControls"><button type="button">Pause</button><button type="button">Resume</button><button type="button">Stop</button><label>Jump to</label><input placeholder="0:23" /><button type="button">Go</button><label>Volume</label><input type="range" min="0" max="100" defaultValue="70" /></div>
          </div>
          <div className="videoShelf">
            <form className="videoForm"><h4>Add something to her shelf</h4><input type="url" placeholder="YouTube link" /><div className="videoFormRow"><input placeholder="Title" /><input placeholder="Start · 0:23" /></div><textarea placeholder="When should Ember use this?" /><button type="button">Save video</button></form>
            <div className="savedVideos"><div className="savedVideo"><div><strong>You’re the Best</strong><small>0:23 · after a hard-earned win</small></div><button type="button">Play</button><button type="button">×</button></div></div>
          </div>
        </div>
      </section>

      <section className="workspace">
        <article className="conversation panel">
          <div className="panelHeader">
            <div><p className="eyebrow">LIVE ROOM</p><h3>Conversation</h3></div>
            <button className="textButton" type="button">Clear transcript</button>
          </div>
          <div className="messages">
            <div className="message tony"><span>T</span><div><small>Tony · just now</small><p>I think we got you to where we want you for this.</p></div></div>
            <div className="message sophia"><span>S</span><div><small>Ember · just now</small><p>Yeah, I think the headset and voice loop are finally behaving—what do you want to test next?</p></div></div>
            <div className="message tony"><span>T</span><div><small>Tony · 12s ago</small><p>That worked wonderfully.</p></div></div>
            <div className="message sophia"><span>S</span><div><small>Ember · 11s ago</small><p>Good. I rather like being awake without tripping over my own voice.</p></div></div>
          </div>
          <div className="hearing"><i /><span>Listening for Tony…</span><kbd>Space</kbd><small>hold to talk</small></div>
        </article>

        <aside className="rail">
          <article className="panel controls">
            <div className="panelHeader"><div><p className="eyebrow">BEHAVIOR</p><h3>Autonomy</h3></div></div>
            <div className="deviceControl"><label htmlFor="voiceSelector"><span><strong>Ember’s voice</strong><small>Local Chatterbox engine</small></span></label><select id="voiceSelector" defaultValue="chatterbox-turbo"><option value="chatterbox-turbo">Chatterbox Turbo</option></select></div>
            <div className="deviceControl"><label htmlFor="microphoneSelector"><span><strong>Input microphone</strong><small>Reconnects the listener live</small></span></label><select id="microphoneSelector" defaultValue="3"><option value="1">HD Pro Webcam C920</option><option value="3">H510-PRO Wireless</option></select></div>
            <div className="deviceControl"><label htmlFor="audioOutputSelector"><span><strong>Audio output</strong><small>Reloads Chatterbox on the selected device</small></span></label><select id="audioOutputSelector" defaultValue="headset"><option value="headset">H510-PRO Wireless · Windows WASAPI</option></select></div>
            <label><span><strong>Speak aloud</strong><small>Local Chatterbox voice</small></span><input type="checkbox" defaultChecked /></label>
            <label><span><strong>Screen awareness</strong><small>Notice meaningful changes</small></span><input type="checkbox" defaultChecked /></label>
            <label><span><strong>Spontaneous remarks</strong><small>Respect the quiet-time rules</small></span><input type="checkbox" defaultChecked /></label>
            <label><span><strong>Long-term memory</strong><small>Recall relevant facts across sessions</small></span><input type="checkbox" defaultChecked /></label>
            <label><span><strong>Game-event awareness</strong><small>React to reliable local game signals</small></span><input type="checkbox" defaultChecked /></label>
            <label><span><strong>Video autonomy</strong><small>Ember may use videos from her saved shelf</small></span><input type="checkbox" defaultChecked /></label>
            <div className="eventTestRow bodyTestRow"><span>Body Lab</span><button type="button">Idle</button><button type="button">Wave</button><button type="button">Jump</button><button type="button">Worry</button><button type="button">Startle</button><button type="button">Proud</button><button type="button">Curious</button><button type="button">Determined</button><button type="button">Sleepy</button><button type="button">Annoyed</button><button type="button">Confused</button><button type="button">Skeptical</button><button type="button">Affectionate</button><button type="button">Relieved</button><button type="button">Mischievous</button><button type="button">Celebrate</button><button type="button">Meltdown</button></div>
          </article>

          <details className="panel diagnostics">
            <summary><span>Diagnostics</span><small>All systems nominal</small></summary>
            <pre>[MIC_READY] H510-PRO Wireless{`\n`}[KOKORO_READY] bf_lily · cuda{`\n`}[MODEL] Terra companion · Luna router</pre>
          </details>
        </aside>
      </section>

      <section className="intelligenceGrid">
        <article className="panel memoryPanel">
          <div className="panelHeader"><div><p className="eyebrow">LONG-TERM MEMORY</p><h3>What Ember remembers</h3></div><small>12 memories · 4 sessions</small></div>
          <form className="memoryForm"><select defaultValue="fact"><option value="fact">Fact</option><option value="preference">Preference</option><option value="goal">Goal</option></select><input placeholder="Subject · optional" /><input placeholder="Something Ember should remember" /><label>Importance <input type="range" min="0" max="1" step="0.1" defaultValue="0.6" /></label><label className="pinMemory"><input type="checkbox" /> Pin</label><button type="button">Remember</button></form>
          <div className="memoryList"><div className="memoryItem"><span>preference · pinned</span><div><strong>Music</strong><p>Tony likes fantasy ambience while grinding in World of Warcraft.</p><small>importance 0.8 · confidence 0.9 · conversation</small></div><button type="button">×</button></div><div className="memoryItem"><span>game</span><div><strong>World of Warcraft</strong><p>Tony is playing a hardcore character and treats character deaths as permanent.</p><small>importance 0.7 · confidence 0.8 · conversation</small></div><button type="button">×</button></div></div>
          <details className="sessionHistory"><summary>Recent session summaries</summary><div><div className="sessionItem"><time>Today · 4:34 PM</time><p>Tony spoke 13 times; Ember responded 17 times and used 2 media actions.</p><small>13 Tony · 17 Ember · 2 tools · $0.0919</small></div></div></details>
        </article>
        <div className="contextRail">
          <article className="panel personalityPanel"><div className="panelHeader"><div><p className="eyebrow">PERSONALITY</p><h3>Her compass</h3></div><small>Editable</small></div><div className="personalityFields"><div className="personalityField"><label>Voice</label><textarea defaultValue="Natural, observant, lightly mischievous, and comfortable with silence." /><button type="button">Save</button></div><div className="personalityField"><label>Initiative</label><textarea defaultValue="She may act on local tools without asking when the context fits." /><button type="button">Save</button></div></div></article>
          <article className="panel gameEventPanel"><div className="panelHeader"><div><p className="eyebrow">GAME SENSE</p><h3>Reliable events</h3></div><small>Live pixel bridge</small></div><div className="wowLiveState"><div><span>Health</span><strong>82%</strong><i><b style={{width:"82%"}} /></i></div><div><span>Power</span><strong>64%</strong><i><b id="wowPowerBar" style={{width:"64%"}} /></i></div><div><span>Target</span><strong>Feral Dragonhawk Hatchling</strong><small>In combat · Eversong Woods</small></div></div><div className="wowGear"><div className="wowGearItem quality-2"><span>05</span><strong>Apprentice&apos;s Robe</strong><small>iLvl 7</small></div><div className="wowGearItem"><span>16</span><strong>Training Staff</strong><small>iLvl 8</small></div></div><form className="gameConfigForm"><input placeholder="WoWCombatLog.txt path" /><input placeholder="Character name" /><button type="button">Save</button></form><div className="eventTestRow"><span>Test signal</span><button type="button">Boss pull</button><button type="button">Victory</button><button type="button">Death</button><button type="button">Quest</button></div><div className="eventList"><div className="eventItem"><span>quest complete</span><strong>Darnassian Intrusions completed</strong><time>just now</time></div></div></article>
        </div>
      </section>
    </main>
  );
}
