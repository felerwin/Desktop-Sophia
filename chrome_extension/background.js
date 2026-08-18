const DASHBOARD_URL = "http://127.0.0.1:8766/";

function isYouTubeUrl(value) {
  try {
    const host = new URL(value).hostname.replace(/^www\./, "");
    return ["youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"].includes(host);
  } catch (_) {
    return false;
  }
}

chrome.action.onClicked.addListener(async (tab) => {
  if (!tab.id || !isYouTubeUrl(tab.url)) {
    await chrome.tabs.create({ url: DASHBOARD_URL });
    return;
  }

  let capture = {
    url: tab.url,
    title: String(tab.title || "").replace(/\s*-\s*YouTube\s*$/, ""),
    seconds: 0,
  };

  try {
    const [result] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: () => ({
        url: window.location.href,
        title: document.title.replace(/\s*-\s*YouTube\s*$/, ""),
        seconds: Math.floor(document.querySelector("video")?.currentTime || 0),
      }),
    });
    if (result?.result) capture = result.result;
  } catch (_) {
    // The tab metadata still gives us enough to fill the form.
  }

  const dashboard = new URL(DASHBOARD_URL);
  dashboard.searchParams.set("youtube_url", capture.url);
  dashboard.searchParams.set("youtube_title", capture.title);
  if (capture.seconds > 0) dashboard.searchParams.set("youtube_start", String(capture.seconds));
  dashboard.hash = "the-tube";
  await chrome.tabs.create({ url: dashboard.toString() });
});
