const API_BASE = window.location.hostname.includes("hf.space") 
  ? "" 
  : "https://7haydar-halalstream.hf.space";

const tabs = Array.from(document.querySelectorAll(".mode-tab"));
const panes = Array.from(document.querySelectorAll(".mode-pane"));
const mediaForm = document.querySelector("#media-form");
const mediaUrl = document.querySelector("#media-url");
const mediaFile = document.querySelector("#media-file");
const fileName = document.querySelector("#file-name");
const recordButton = document.querySelector("#record-button");
const recordStatus = document.querySelector("#record-status");
const recordPreview = document.querySelector("#record-preview");
const submitButton = document.querySelector("#submit-button");
const purifyButton = document.querySelector("#purify-button");
const decisionOverlay = document.querySelector("#decision-overlay");
const decisionPurifyButton = document.querySelector("#decision-purify-button");
const retryButton = document.querySelector("#retry-button");
const formError = document.querySelector("#form-error");
const hostingNote = document.querySelector("#hosting-note");
const serverPill = document.querySelector("#server-pill");
const serverText = document.querySelector("#server-text");
const jobHeading = document.querySelector("#job-heading");
const statusTitle = document.querySelector("#status-title");
const statusMessage = document.querySelector("#status-message");
const progressBar = document.querySelector("#progress-bar");
const cleanCard = document.querySelector("#clean-card");
const warningCard = document.querySelector("#warning-card");
const completeCard = document.querySelector("#complete-card");
const errorCard = document.querySelector("#error-card");
const errorMessage = document.querySelector("#error-message");
const localHelper = document.querySelector("#local-helper");
const helperCommand = document.querySelector("#helper-command");
const copyHelperCommand = document.querySelector("#copy-helper-command");
const downloadHelperScript = document.querySelector("#download-helper-script");
const uploadLocalFile = document.querySelector("#upload-local-file");
const cleanVideoDownload = document.querySelector("#clean-video-download");
const cleanAudioDownload = document.querySelector("#clean-audio-download");
const purifiedVideoDownload = document.querySelector("#purified-video-download");
const purifiedAudioDownload = document.querySelector("#purified-audio-download");
const metricProgress = document.querySelector("#metric-progress");
const metricSignal = document.querySelector("#metric-signal");
const metricTime = document.querySelector("#metric-time");
const processLog = document.querySelector(".process-log");
const engineMode = document.querySelector("#engine-mode");
const engineJobs = document.querySelector("#engine-jobs");
const stageSteps = Array.from(document.querySelectorAll(".stage-step"));

let activeMode = "link";
let recorder = null;
let recordedBlob = null;
let recordedChunks = [];
let recordingStream = null;
let currentJobId = null;
let pollTimer = null;
let elapsedTimer = null;
let jobStartedAt = null;
let lastLogMessage = "";
let serverReady = false;
let linkDownloadsReliable = true;

const waitingNotes = [
  "يمكنك ترك الصفحة مفتوحة والرجوع لاحقاً؛ خادم المعالجة سيكمل العمل ما دام السيرفر شغالاً.",
  "استغل وقت الانتظار بالاستغفار: أستغفر الله وأتوب إليه.",
  "قال تعالى: {وَمَنْ يَتَّقِ اللَّهَ يَجْعَلْ لَهُ مَخْرَجًا}.",
  "يُمسخُ قومٌ من أمتي في آخرِ الزمانِ قِرَدةً وخنازيرَ، قيل : يا رسولَ اللهِ ويشهدونَ أنْ لا إلهَ إلا اللهُ وأنك رسولُ اللهِ ويصومون ؟ قال : نعم. قيل : فما بالُهم يا رسولَ اللهِ ؟ قال : يتخذونَ المعازفَ والقيناتِ والدفوفَ ويشربونَ الأشربةَ فباتوا على شُربِهم ولهوِهم، فأصبحوا وقد مُسِخوا قِرَدةً وخنازيرَ",
  "نحاول إبقاء الصوت البشري وحذف مسار المعازف قدر الإمكان.",
  "دع قلبك يستريح بذكر الله حتى يكتمل العمل."
];

const statusToStep = {
  queued: "receive",
  downloading: "download",
  extracting: "download",
  separating: "separate",
  analyzing: "separate",
  needs_consent: "decision",
  clean: "delivery",
  purifying: "separate",
  complete: "delivery",
  failed: "decision"
};

tabs.forEach((tab) => {
  tab.addEventListener("click", () => setMode(tab.dataset.mode));
});

mediaFile.addEventListener("change", () => {
  fileName.textContent = mediaFile.files[0]?.name || "لم يتم اختيار ملف بعد";
});

// Toggle purify sub-options and warning box depending on selected mode
const purifyModeRadios = document.querySelectorAll('input[name="purify_mode"]');
const purifyQualityOptions = document.querySelector("#purify-quality-options");
const directWarningBox = document.querySelector("#direct-warning-box");

purifyModeRadios.forEach((radio) => {
  radio.addEventListener("change", () => {
    if (radio.value === "purify") {
      purifyQualityOptions.style.display = "flex";
      directWarningBox.hidden = true;
    } else {
      purifyQualityOptions.style.display = "none";
      directWarningBox.hidden = false;
    }
  });
});

mediaForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  clearError();

  if (!serverReady) {
    showError("خادم المعالجة غير متصل حالياً. يرجى فتح السيرفر وإيقاظه بالضغط على الرابط أسفل لوحة الانتظار.");
    return;
  }

  try {
    setBusy(true);
    hideResultCards();
    resetLog("بدأنا مهمة جديدة. نسأل الله التيسير والبركة.");
    startElapsedTimer();
    setStage("receive");
    updateStatus("بدء الفحص", "نرسل المقطع إلى خادم المعالجة ونجهز مهمة المعالجة.", 4);

    const jobId = await createJob();
    currentJobId = jobId;
    try {
      localStorage.setItem("halalstream_job_id", jobId);
    } catch (e) {
      // Ignored if localStorage is disabled
    }
    startPolling(jobId);
  } catch (error) {
    setBusy(false);
    showError(error.message || "تعذر إرسال المقطع إلى خادم المعالجة.");
  }
});

purifyButton.addEventListener("click", startPurify);
decisionPurifyButton.addEventListener("click", startPurify);

async function startPurify() {
  if (!currentJobId) {
    showError("لا توجد مهمة جاهزة لإزالة المعازف.");
    return;
  }

  try {
    clearError();
    setBusy(true);
    hideResultCards();
    updateStatus("بدء إزالة المعازف", "تمت الموافقة. نبدأ تجهيز نسخة منقّاة بالصوت البشري قدر الإمكان.", 82);
    appendLog("تمت الموافقة على إزالة المعازف. يمكنك الاستغفار حتى يكتمل العمل.");
    const response = await fetch(`${API_BASE}/api/jobs/${currentJobId}/purify`, { method: "POST" });
    if (!response.ok) {
      throw new Error(await readError(response));
    }
    startPolling(currentJobId);
  } catch (error) {
    setBusy(false);
    showError(error.message || "تعذر بدء إزالة المعازف.");
  }
}

retryButton.addEventListener("click", async () => {
  if (!currentJobId) {
    showError("لا توجد مهمة لإعادة المحاولة.");
    return;
  }

  try {
    clearError();
    setBusy(true);
    hideResultCards();
    resetLog("نعيد المحاولة من الملف المحفوظ لتوفير وقت التحميل.");
    startElapsedTimer();
    updateStatus("إعادة المحاولة", "نستخدم الملف الموجود ونبدأ استخراج الصوت من جديد.", 3);
    const response = await fetch(`${API_BASE}/api/jobs/${currentJobId}/retry`, { method: "POST" });
    if (!response.ok) {
      throw new Error(await readError(response));
    }
    startPolling(currentJobId);
  } catch (error) {
    setBusy(false);
    showError(error.message || "تعذرت إعادة المحاولة.");
  }
});

copyHelperCommand.addEventListener("click", async () => {
  if (!helperCommand.value) return;
  try {
    await navigator.clipboard.writeText(helperCommand.value);
    copyHelperCommand.textContent = "تم النسخ";
    window.setTimeout(() => {
      copyHelperCommand.textContent = "نسخ أمر Windows";
    }, 1800);
  } catch (error) {
    helperCommand.focus();
    helperCommand.select();
  }
});

downloadHelperScript.addEventListener("click", () => {
  if (!helperCommand.value) return;
  const blob = new Blob([helperCommand.value], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = "halalstream-local-download.ps1";
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
});

uploadLocalFile.addEventListener("click", () => {
  setMode("file");
  mediaForm.scrollIntoView({ behavior: "smooth", block: "center" });
  window.setTimeout(() => mediaFile.click(), 260);
});

recordButton.addEventListener("click", async () => {
  clearError();

  if (recorder && recorder.state === "recording") {
    recorder.stop();
    return;
  }

  if (!navigator.mediaDevices || !window.MediaRecorder) {
    showError("المتصفح لا يدعم التسجيل المباشر حالياً. يمكنك رفع ملف صوتي بدلاً من ذلك.");
    return;
  }

  try {
    recordedChunks = [];
    recordedBlob = null;
    recordingStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    recorder = new MediaRecorder(recordingStream);

    recorder.addEventListener("dataavailable", (event) => {
      if (event.data.size > 0) {
        recordedChunks.push(event.data);
      }
    });

    recorder.addEventListener("stop", () => {
      recordingStream.getTracks().forEach((track) => track.stop());
      recordedBlob = new Blob(recordedChunks, { type: recorder.mimeType || "audio/webm" });
      recordPreview.src = URL.createObjectURL(recordedBlob);
      recordPreview.hidden = false;
      recordButton.textContent = "إعادة التسجيل";
      recordStatus.textContent = "تم حفظ التسجيل، ويمكنك بدء الفحص الآن.";
    });

    recorder.start();
    recordButton.textContent = "إيقاف التسجيل";
    recordStatus.textContent = "جار التسجيل الآن.";
  } catch (error) {
    showError("تعذر الوصول إلى الميكروفون. تحقق من إذن المتصفح ثم حاول مرة أخرى.");
  }
});

checkHealth();

async function checkHealth() {
  try {
    const response = await fetch(`${API_BASE}/api/health`, { cache: "no-store" });
    if (!response.ok) {
      throw new Error("health failed");
    }
    const health = await response.json();
    serverReady = Boolean(health.ok && health.ffmpeg && health.yt_dlp && health.demucs);
    linkDownloadsReliable = health.link_downloads_reliable !== false;
    hostingNote.hidden = linkDownloadsReliable;
    serverPill.classList.toggle("is-ready", serverReady);
    serverPill.classList.toggle("is-error", !serverReady);
    engineMode.textContent = "جودة عالية";
    engineJobs.textContent = linkDownloadsReliable ? "ثابت" : "رفع الملفات أفضل";

    if (serverReady) {
      serverText.textContent = "الخادم يعمل";
      if (!linkDownloadsReliable && activeMode === "link") {
        setMode("file");
      }
      updateStatus(
        "خادم المعالجة جاهز",
        linkDownloadsReliable
          ? "أرسل رابطاً أو ملفاً، وسنوقف التحميل إن ظهرت معازف حتى تختار إزالتها."
          : "ارفع ملفاً من جهازك للحصول على نتيجة أثبت.",
        0
      );
      resetLog(
        linkDownloadsReliable
          ? "الخادم جاهز. أرسل المقطع، ويمكنك متابعة الصفحة أو الرجوع لها لاحقاً."
          : "الخادم جاهز. رفع الملف مباشرة هو المسار الأنسب لهذه الاستضافة."
      );
      await restoreLatestJob();
      return;
    }

    const missing = [];
    if (!health.yt_dlp) missing.push("yt-dlp");
    if (!health.ffmpeg) missing.push("ffmpeg");
    if (!health.demucs) missing.push("demucs");
    serverText.textContent = `ينقص: ${missing.join("، ")}`;
    updateStatus("الخادم يعمل لكن المتطلبات ناقصة", `ثبّت المتطلبات ثم أعد تشغيل السيرفر. العناصر الناقصة: ${missing.join("، ")}.`, 0);
  } catch (error) {
    serverReady = false;
    serverPill.classList.add("is-error");
    serverPill.classList.remove("is-ready");
    serverText.textContent = "الخادم غير متصل";
    const wakeUpUrl = "https://7haydar-halalstream.hf.space";
    updateStatus(
      "السيرفر نائم حالياً 😴",
      `السيرفر مطفأ تلقائياً لتوفير التكلفة أثناء عدم الاستخدام. <a href="${wakeUpUrl}" target="_blank" style="color: #b68134; font-weight: bold; text-decoration: underline; display: inline-block; margin-top: 4px;">انقر هنا لفتح السيرفر وإيقاظه في نافذة جديدة</a>، ثم انتظر 30 ثانية وأعد تحديث هذه الصفحة.`,
      0
    );
  }
}

async function restoreLatestJob() {
  try {
    const savedJobId = localStorage.getItem("halalstream_job_id");
    if (!savedJobId) {
      return;
    }
    const response = await fetch(`${API_BASE}/api/jobs/${savedJobId}`, { cache: "no-store" });
    if (!response.ok) {
      return;
    }
    const job = await response.json();
    currentJobId = job.id;
    renderJob(job);
    appendLog("استعدنا آخر مهمة محفوظة لجهازك.");
  } catch (error) {
    // لا نزعج المستخدم إذا لم تكن هناك مهمة سابقة.
  }
}

async function createJob() {
  const purifyModeVal = document.querySelector('input[name="purify_mode"]:checked').value;
  const qualityVal = document.querySelector('input[name="quality"]:checked').value;

  if (activeMode === "link") {
    const rawUrl = mediaUrl.value.trim();
    if (!rawUrl) {
      throw new Error("ضع رابط المقطع أولاً.");
    }
    const url = /^[a-z][a-z0-9+.-]*:\/\//i.test(rawUrl) ? rawUrl : `https://${rawUrl}`;
    if (!linkDownloadsReliable && isYouTubeUrl(url)) {
      throw new Error("روابط YouTube لا تعمل بثبات على الاستضافة الحالية. نزّل الملف على جهازك ثم ارفعه من تبويب ملف.");
    }
    const response = await fetch(`${API_BASE}/api/jobs/link`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        url,
        purify_mode: purifyModeVal,
        quality: qualityVal
      })
    });
    if (!response.ok) {
      throw new Error(await readError(response));
    }
    const payload = await response.json();
    return payload.id;
  }

  const formData = new FormData();
  formData.append("purify_mode", purifyModeVal);
  formData.append("quality", qualityVal);

  if (activeMode === "file") {
    const file = mediaFile.files[0];
    if (!file) {
      throw new Error("اختر ملفاً من جهازك أولاً.");
    }
    formData.append("file", file);
  } else {
    if (!recordedBlob) {
      throw new Error("سجّل صوتاً أولاً ثم ابدأ الفحص.");
    }
    formData.append("file", recordedBlob, "recorded-voice.webm");
  }

  const response = await fetch(`${API_BASE}/api/jobs/upload`, {
    method: "POST",
    body: formData
  });
  if (!response.ok) {
    throw new Error(await readError(response));
  }
  const payload = await response.json();
  return payload.id;
}

function startPolling(jobId) {
  window.clearInterval(pollTimer);
  pollTimer = window.setInterval(() => pollJob(jobId), 1300);
  pollJob(jobId);
}

async function pollJob(jobId) {
  try {
    const response = await fetch(`${API_BASE}/api/jobs/${jobId}`, { cache: "no-store" });
    if (!response.ok) {
      throw new Error(await readError(response));
    }
    const job = await response.json();
    renderJob(job);
    if (["clean", "needs_consent", "complete", "failed"].includes(job.status)) {
      window.clearInterval(pollTimer);
      pollTimer = null;
      setBusy(false);
      stopElapsedTimer();
    }
  } catch (error) {
    window.clearInterval(pollTimer);
    pollTimer = null;
    setBusy(false);
    showError(error.message || "تعذر قراءة حالة المهمة.");
  }
}

function renderJob(job) {
  const title = job.title || "مقطع قيد المعالجة";
  jobHeading.textContent = title;
  setStage(statusToStep[job.status] || "receive");
  updateStatus(humanStage(job), humanMessage(job), job.progress || 0);
  updateSignalMetric(job);
  appendLog(humanLog(job));
  hideResultCards();

  if (job.status === "clean") {
    setDownloadLink(cleanVideoDownload, job.download_urls?.video || job.download_url);
    setDownloadLink(cleanAudioDownload, job.download_urls?.audio);
    cleanCard.hidden = false;
    return;
  }

  if (job.status === "needs_consent") {
    showDecisionOverlay();
    const ratioPct = Math.round((job.instrumental_ratio || 0) * 100);
    const warningRatioEl = document.querySelector("#warning-ratio");
    if (warningRatioEl) {
      warningRatioEl.textContent = `نسبة الموسيقى/المعازف المرصودة: ${ratioPct}%`;
      warningRatioEl.hidden = false;
    }
    warningCard.hidden = false;

    // Auto-scroll to the warning card so the user notices the action on mobile
    window.setTimeout(() => {
      warningCard.scrollIntoView({ behavior: "smooth", block: "center" });
    }, 150);
    return;
  }

  if (job.status === "complete") {
    setDownloadLink(purifiedVideoDownload, job.download_urls?.video || job.download_url);
    setDownloadLink(purifiedAudioDownload, job.download_urls?.audio);
    const ratioPct = Math.round((job.instrumental_ratio || 0) * 100);
    const completeRatioEl = document.querySelector("#complete-ratio");
    if (completeRatioEl) {
      completeRatioEl.textContent = `نسبة المعازف قبل التنقية: ${ratioPct}% | بعد التنقية: 0% (منقّى بالكامل)`;
      completeRatioEl.hidden = false;
    }
    completeCard.hidden = false;
    return;
  }

  if (job.status === "failed") {
    errorMessage.textContent = job.message || "حدث خطأ غير متوقع أثناء المعالجة.";
    renderLocalHelper(job);
    errorCard.hidden = false;
  }
}

function setMode(mode) {
  activeMode = mode;
  clearError();

  tabs.forEach((tab) => {
    const selected = tab.dataset.mode === mode;
    tab.classList.toggle("is-selected", selected);
    tab.setAttribute("aria-selected", String(selected));
  });

  panes.forEach((pane) => {
    pane.classList.toggle("is-visible", pane.dataset.pane === mode);
  });
}

function updateStatus(title, message, progress) {
  const safeProgress = Math.max(0, Math.min(100, Number(progress) || 0));
  statusTitle.textContent = title;
  statusMessage.innerHTML = message;
  progressBar.style.width = `${safeProgress}%`;
  metricProgress.textContent = `${safeProgress}%`;
}

function setStage(activeStep) {
  const order = ["receive", "download", "separate", "decision", "delivery"];
  const activeIndex = order.indexOf(activeStep);

  stageSteps.forEach((step) => {
    const stepIndex = order.indexOf(step.dataset.step);
    step.classList.toggle("is-active", step.dataset.step === activeStep);
    step.classList.toggle("is-complete", activeIndex > -1 && stepIndex < activeIndex);
  });
}

function hideResultCards() {
  cleanCard.hidden = true;
  warningCard.hidden = true;
  completeCard.hidden = true;
  errorCard.hidden = true;
  localHelper.hidden = true;
  hideDecisionOverlay();
}

function showDecisionOverlay() {
  // Disabled full-screen popup to prevent layout/scrolling issues on mobile.
  // The warning card is displayed directly inside the page instead.
  decisionOverlay.hidden = true;
}

function hideDecisionOverlay() {
  decisionOverlay.hidden = true;
}

function setDownloadLink(anchor, url) {
  if (!url) {
    anchor.removeAttribute("href");
    anchor.classList.add("is-disabled");
    return;
  }

  anchor.href = url.startsWith("http") ? url : `${API_BASE}${url}`;
  anchor.classList.remove("is-disabled");
}

function updateSignalMetric(job) {
  if (job.status === "complete") {
    metricSignal.textContent = "منقّى";
    return;
  }
  if (job.status === "needs_consent") {
    metricSignal.textContent = "رُصدت معازف";
    return;
  }
  if (job.status === "clean") {
    metricSignal.textContent = "سليم";
    return;
  }
  if (job.status === "failed") {
    metricSignal.textContent = "تحتاج مراجعة";
    return;
  }

  metricSignal.textContent = "جار العمل";
}

function humanStage(job) {
  if (job.status === "needs_consent") return "رُصدت معازف";
  if (job.status === "complete") return "تمت إزالة المعازف";
  if (job.status === "clean") return "المقطع سليم";
  if (job.status === "purifying") return "إزالة المعازف";
  if (job.status === "separating") return "عزل الصوت";
  if (job.status === "downloading") return "تحميل المقطع";
  if (job.status === "extracting") return "استخراج الصوت";
  return job.stage || "جار العمل";
}

function humanMessage(job) {
  if (job.status === "downloading") {
    return "نحمّل المقطع إلى خادم المعالجة. إن طال الانتظار، فتبويب ملف أسرع وأثبت غالباً.";
  }
  if (job.status === "extracting") {
    return "نستخرج الصوت من المقطع حتى نبدأ فحص مسار المعازف.";
  }
  if (job.status === "separating" || job.status === "analyzing") {
    return `${pickWaitingNote()} جاري المعالجة بالذكاء الاصطناعي، لن يستغرق الأمر سوى لحظات يسيرة.`;
  }
  if (job.status === "needs_consent") {
    return "⚠️ تم رصد مسار معازف! أوقفنا التحميل. يرجى النزول لأسفل لوحة المعالجة والموافقة لإكمال عملية التطهير.";
  }
  if (job.status === "purifying") {
    return `${pickWaitingNote()} نجهز النسخة المنقّاة الآن.`;
  }
  if (job.status === "complete") {
    return "تم بحمد الله عزل مسار المعازف وإعداد نسخة منقّاة قدر الإمكان. اختر تحميل المقطع أو الصوت فقط.";
  }
  if (job.status === "clean") {
    return "الحمد لله، لم يظهر مؤشر معتبر للمعازف. يمكنك تحميل المقطع أو الصوت فقط.";
  }
  return job.message || "خادم المعالجة يعالج المقطع الآن.";
}

function humanLog(job) {
  if (job.status === "downloading") return "تحميل المقطع: روابط YouTube قد تتأخر؛ رفع الملف مباشرة يكون أسرع غالباً.";
  if (job.status === "extracting") return "استخراج الصوت: نجهز المسار الصوتي للفحص.";
  if (job.status === "separating") return `عزل الصوت: ${pickWaitingNote()}`;
  if (job.status === "analyzing") return "مراجعة النتيجة: نتحقق قبل السماح بالتحميل.";
  if (job.status === "needs_consent") return "قرار الفحص: رُصدت معازف، والتحميل متوقف حتى توافق على الإزالة.";
  if (job.status === "purifying") return "إزالة المعازف: نجهز النسخة المنقّاة.";
  if (job.status === "complete") return "تمت إزالة المعازف: الملف المنقّى جاهز للتحميل.";
  if (job.status === "clean") return "الحمد لله: المقطع سليم وجاهز للتحميل.";
  return job.message || "جار العمل.";
}

function pickWaitingNote() {
  const seconds = Math.floor(Date.now() / 1000);
  return waitingNotes[Math.floor(seconds / 8) % waitingNotes.length];
}

function isYouTubeUrl(url) {
  const lowered = url.toLowerCase();
  return lowered.includes("youtube.com") || lowered.includes("youtu.be");
}

function renderLocalHelper(job) {
  localHelper.hidden = true;
  helperCommand.value = "";

  const url = job.source_url || mediaUrl.value.trim();
  if (!url || !isYouTubeUrl(url) || !needsLocalDownload(job.message || "")) {
    return;
  }

  helperCommand.value = buildWindowsLocalDownloader(url);
  helperCommand.scrollTop = 0;
  localHelper.hidden = false;
}

function needsLocalDownload(message) {
  const text = message.toLowerCase();
  return text.includes("youtube") || text.includes("كوكيز") || text.includes("بروكسي") || text.includes("جلسة");
}

function buildWindowsLocalDownloader(sourceUrl) {
  const siteUrl = `${location.origin}/`;
  const lines = [
    "$ErrorActionPreference = 'Stop'",
    "$dir = Join-Path $env:USERPROFILE 'Downloads\\HalalStream'",
    "New-Item -ItemType Directory -Force -Path $dir | Out-Null",
    "$tool = Join-Path $dir 'yt-dlp.exe'",
    "if (!(Test-Path $tool)) {",
    "  Invoke-WebRequest -UseBasicParsing -Uri 'https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe' -OutFile $tool",
    "}",
    "$out = Join-Path $dir '%(title).120s-%(id)s.%(ext)s'",
    `$url = ${toPowerShellSingleQuoted(sourceUrl)}`,
    "$browsers = @('edge', 'chrome', 'firefox')",
    "$done = $false",
    "foreach ($browser in $browsers) {",
    "  Write-Host \"نجرب التحميل عبر جلسة المتصفح: $browser\"",
    "  & $tool --cookies-from-browser $browser -f \"bv*[height<=720]+ba/b[height<=720]/best\" --merge-output-format mp4 -o $out $url",
    "  if ($LASTEXITCODE -eq 0) { $done = $true; break }",
    "}",
    "if (!$done) {",
    "  Write-Host 'نجرب التحميل المباشر بدون كوكيز المتصفح.'",
    "  & $tool -f \"bv*[height<=720]+ba/b[height<=720]/best\" --merge-output-format mp4 -o $out $url",
    "  if ($LASTEXITCODE -eq 0) { $done = $true }",
    "}",
    "if (!$done) { throw 'تعذر التحميل المحلي أيضاً. افتح رابط YouTube في المتصفح مرة واحدة ثم أعد تشغيل السكربت.' }",
    `Start-Process ${toPowerShellSingleQuoted(siteUrl)}`,
    "Write-Host ''",
    "Write-Host 'تم التحميل داخل مجلد Downloads\\HalalStream. ارجع للموقع واختر الملف من تبويب ملف.'"
  ];
  return lines.join("\n");
}

function toPowerShellSingleQuoted(value) {
  return `'${String(value).replaceAll("'", "''")}'`;
}

function startElapsedTimer() {
  stopElapsedTimer();
  jobStartedAt = Date.now();
  metricTime.textContent = "00:00";
  elapsedTimer = window.setInterval(() => {
    const seconds = Math.floor((Date.now() - jobStartedAt) / 1000);
    metricTime.textContent = formatTime(seconds);
  }, 1000);
}

function stopElapsedTimer() {
  if (elapsedTimer) {
    window.clearInterval(elapsedTimer);
    elapsedTimer = null;
  }
}

function formatTime(seconds) {
  const minutes = Math.floor(seconds / 60).toString().padStart(2, "0");
  const rest = Math.floor(seconds % 60).toString().padStart(2, "0");
  return `${minutes}:${rest}`;
}

function resetLog(message) {
  processLog.innerHTML = "";
  lastLogMessage = "";
  appendLog(message);
}

function appendLog(message) {
  if (!message || message === lastLogMessage) {
    return;
  }

  lastLogMessage = message;
  const row = document.createElement("div");
  row.className = "log-row is-current";
  row.textContent = message;
  processLog.querySelectorAll(".log-row").forEach((item) => item.classList.remove("is-current"));
  processLog.prepend(row);

  Array.from(processLog.querySelectorAll(".log-row"))
    .slice(5)
    .forEach((item) => item.remove());
}

function setBusy(isBusy) {
  submitButton.disabled = isBusy;
  purifyButton.disabled = isBusy;
  decisionPurifyButton.disabled = isBusy;
  retryButton.disabled = isBusy;
}

function showError(message) {
  formError.textContent = message;
  formError.hidden = false;
}

function clearError() {
  formError.textContent = "";
  formError.hidden = true;
}

async function readError(response) {
  try {
    const payload = await response.json();
    return payload.detail || "حدث خطأ في الخادم.";
  } catch (error) {
    return "حدث خطأ في الخادم.";
  }
}
