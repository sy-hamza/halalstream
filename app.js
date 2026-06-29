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

const waitingNotes = [
  "يمكنك ترك الصفحة مفتوحة والرجوع لاحقاً؛ خادم المعالجة سيكمل العمل ما دام السيرفر شغالاً.",
  "استغل وقت الانتظار بالاستغفار: أستغفر الله وأتوب إليه.",
  "قال تعالى: {وَمَنْ يَتَّقِ اللَّهَ يَجْعَلْ لَهُ مَخْرَجًا}.",
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

mediaForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  clearError();

  if (!serverReady) {
    showError("خادم المعالجة غير متصل. إن كنت تعمل محلياً شغّل run-local.bat ثم افتح http://127.0.0.1:8000");
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
    const response = await fetch(`/api/jobs/${currentJobId}/purify`, { method: "POST" });
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
    const response = await fetch(`/api/jobs/${currentJobId}/retry`, { method: "POST" });
    if (!response.ok) {
      throw new Error(await readError(response));
    }
    startPolling(currentJobId);
  } catch (error) {
    setBusy(false);
    showError(error.message || "تعذرت إعادة المحاولة.");
  }
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
    const response = await fetch("/api/health", { cache: "no-store" });
    if (!response.ok) {
      throw new Error("health failed");
    }
    const health = await response.json();
    serverReady = Boolean(health.ok && health.ffmpeg && health.yt_dlp && health.demucs);
    serverPill.classList.toggle("is-ready", serverReady);
    serverPill.classList.toggle("is-error", !serverReady);
    engineMode.textContent = "جودة عالية";
    engineJobs.textContent = "ثابت";

    if (serverReady) {
      serverText.textContent = "الخادم يعمل";
      updateStatus("خادم المعالجة جاهز", "أرسل رابطاً أو ملفاً، وسنوقف التحميل إن ظهرت معازف حتى تختار إزالتها.", 0);
      resetLog("الخادم جاهز. أرسل المقطع، ويمكنك متابعة الصفحة أو الرجوع لها لاحقاً.");
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
    updateStatus(
      "شغّل خادم المعالجة أولاً",
      "افتح التطبيق من رابط الخادم. إن كنت تعمل محلياً شغّل run-local.bat ثم ادخل إلى http://127.0.0.1:8000.",
      0
    );
  }
}

async function restoreLatestJob() {
  try {
    const response = await fetch("/api/jobs/latest", { cache: "no-store" });
    if (!response.ok) {
      return;
    }
    const job = await response.json();
    currentJobId = job.id;
    renderJob(job);
    appendLog("استعدنا آخر مهمة محفوظة من الخادم.");
  } catch (error) {
    // لا نزعج المستخدم إذا لم تكن هناك مهمة سابقة.
  }
}

async function createJob() {
  if (activeMode === "link") {
    const rawUrl = mediaUrl.value.trim();
    if (!rawUrl) {
      throw new Error("ضع رابط المقطع أولاً.");
    }
    const url = /^[a-z][a-z0-9+.-]*:\/\//i.test(rawUrl) ? rawUrl : `https://${rawUrl}`;
    const response = await fetch("/api/jobs/link", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url })
    });
    if (!response.ok) {
      throw new Error(await readError(response));
    }
    const payload = await response.json();
    return payload.id;
  }

  const formData = new FormData();
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

  const response = await fetch("/api/jobs/upload", {
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
    const response = await fetch(`/api/jobs/${jobId}`, { cache: "no-store" });
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
    warningCard.hidden = false;
    return;
  }

  if (job.status === "complete") {
    setDownloadLink(purifiedVideoDownload, job.download_urls?.video || job.download_url);
    setDownloadLink(purifiedAudioDownload, job.download_urls?.audio);
    completeCard.hidden = false;
    return;
  }

  if (job.status === "failed") {
    errorMessage.textContent = job.message || "حدث خطأ غير متوقع أثناء المعالجة.";
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
  statusMessage.textContent = message;
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
  hideDecisionOverlay();
}

function showDecisionOverlay() {
  decisionOverlay.hidden = false;
  document.body.classList.add("has-decision-overlay");
  window.setTimeout(() => decisionPurifyButton.focus({ preventScroll: true }), 80);
}

function hideDecisionOverlay() {
  decisionOverlay.hidden = true;
  document.body.classList.remove("has-decision-overlay");
}

function setDownloadLink(anchor, url) {
  if (!url) {
    anchor.removeAttribute("href");
    anchor.classList.add("is-disabled");
    return;
  }

  anchor.href = url;
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
    return "نحمّل المقطع إلى خادم المعالجة. إن طال انتظار رابط YouTube على الاستضافة المجانية، فتبويب ملف أسرع وأثبت غالباً.";
  }
  if (job.status === "extracting") {
    return "نستخرج الصوت من المقطع حتى نبدأ فحص مسار المعازف.";
  }
  if (job.status === "separating" || job.status === "analyzing") {
    return `${pickWaitingNote()} هذه المرحلة قد تأخذ عدة دقائق حسب طول المقطع وقوة الجهاز.`;
  }
  if (job.status === "needs_consent") {
    return "تم رصد مسار معازف؛ أوقفنا التحميل حتى تختار إزالة المعازف.";
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
  if (job.status === "downloading") return "تحميل المقطع: روابط YouTube قد تتأخر على الاستضافة المجانية؛ رفع الملف مباشرة يكون أسرع غالباً.";
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
