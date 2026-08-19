# ملف تسليم HalalStream لكودكس على جهاز جديد

هذا الملف معمول حتى تنسخ مشروع `F:\clleanm` إلى جهاز آخر وتبدأ محادثة Codex جديدة وهي فاهمة السياق بدون التخبيص في الأشياء الحساسة.

لا يحتوي هذا الملف على كلمات سر أو أسرار Modal/Hugging Face. لا تضع الأسرار في Git أبداً.

## برومبت جاهز انسخه لكودكس الجديد

انسخ النص التالي في أول رسالة لكودكس على الجهاز الجديد بعد ما تفتح مجلد المشروع:

```text
مرحبا Codex. هذا مشروع HalalStream لإزالة المعازف من المقاطع، ومهم جداً أن تحافظ على السلوك الحالي ولا تكسر الموقع.

ابدأ بقراءة الملف CODEX_HANDOFF_AR.md كاملاً ثم افحص الملفات الأساسية:
- server.py
- app.js
- modal_worker.py
- README.md
- MODAL.md

قواعد مهمة:
- المشروع إسلامي، والهدف تنزيل/تنقية المقاطع مع تجنب تمرير المعازف قدر الإمكان.
- لا تعد بشيء اسمه إزالة 100% تقنياً، لكن عند الشك يجب أن تفشل بأمان ولا تخرج ملفاً مشبوهاً.
- خيار "تحميل دون فحص" أو purify_mode=direct يجب أن يبقى تحميل مباشر فقط، بدون فحص وبدون تنقية وبدون Modal وبدون صرف كريدت.
- التنقية فقط هي التي تستخدم Modal GPU عند ضبط HALALSTREAM_MODAL_PURIFY_URL و HALALSTREAM_MODAL_SECRET.
- لوحة الأدمن /admin/usage فيها عداد استهلاك تقديري وزر إيقاف/تشغيل التنقية. إيقاف التنقية يمنع وظائف التنقية الجديدة فقط ولا يوقف التحميل المباشر.
- حد روابط التنقية 10 دقائق حالياً عبر HALALSTREAM_MAX_LINK_DURATION_SECONDS=600.
- روابط Instagram/Reels قد تعيد صورة jpg بدل الفيديو؛ يوجد فحص في server.py يمنع الصور والملفات بلا صوت من الوصول إلى Modal.
- لا تعرض رسائل ffmpeg الطويلة للمستخدم؛ friendly_error يجب أن يعطي رسالة عربية قصيرة.
- لا تلمس index.html إذا كان فيه تعديل محلي غير متعلق بالمهمة إلا بعد سؤال المستخدم.
- عند التعديل استخدم apply_patch، اختبر محلياً، ثم ارفع إلى GitHub وHugging Face إذا طلب المستخدم النشر.

قبل أي تعديل شغل:
git status --short
rg -n "اسم الشيء الذي ستعدله" .

بعد أي تعديل في الخادم شغل:
.\.venv\Scripts\python.exe -m py_compile server.py modal_worker.py
git diff --check -- server.py app.js

النشر الحالي:
- الموقع العام: https://halalstream.me/
- Hugging Face Space: https://7haydar-halalstream.hf.space
- GitHub remote origin: https://github.com/sy-hamza/halalstream.git
- Hugging Face remote hf: https://huggingface.co/spaces/7HAYDAR/halalstream

لا تكتب أسرار الأدمن أو Modal في الملفات. الأسرار موجودة في إعدادات Hugging Face/Modal ويجب إدخالها من لوحاتهم فقط.
```

## فكرة المشروع باختصار

HalalStream موقع عربي يتيح للمستخدم:

- وضع رابط أو رفع ملف صوت/فيديو.
- اختيار تنقية المعازف أو التحميل المباشر.
- تنزيل الملف بعد التنقية أو تنزيله كما هو في وضع direct.

الخادم مبني بـ FastAPI في `server.py`. الواجهة في `index.html` و `app.js` و `styles.css`. عامل GPU الخارجي في `modal_worker.py`.

## الاستضافة الحالية

- الدومين العام: `https://halalstream.me/`
- الواجهة على GitHub Pages مع ملف `CNAME`.
- `app.js` يوجه طلبات API إلى Hugging Face عندما لا يكون الموقع مفتوحاً من نطاق `hf.space`.
- خادم Hugging Face Space: `https://7haydar-halalstream.hf.space`
- التنقية الثقيلة تعمل على Modal عند الطلب، حتى لا ندفع GPU دائم على Hugging Face.

الأوامر المعتادة للنشر:

```powershell
git push origin main
git push hf main
```

إذا الجهاز الجديد لا يعرف remote الخاص بـ Hugging Face:

```powershell
git remote add hf https://huggingface.co/spaces/7HAYDAR/halalstream
```

## أهم الملفات

- `server.py`: FastAPI، تنزيل الروابط، إدارة المهام، التنقية، لوحة الأدمن، عداد الاستهلاك، زر إيقاف التنقية.
- `app.js`: منطق الواجهة، إرسال المهام، polling، التعامل مع حالة الخادم والتنقية المتوقفة.
- `modal_worker.py`: عامل Modal GPU لتنقية الصوت.
- `README.md`: تشغيل محلي وملاحظات عامة.
- `MODAL.md`: طريقة نشر Modal والأسرار المطلوبة.
- `admin/usage/index.html`: صفحة تحويل من `halalstream.me/admin/usage/` إلى لوحة الأدمن على Hugging Face.
- `storage/`: ملفات مهام ونتائج وسجلات محلية. لا ترفعها إلى Git.

## متغيرات البيئة المهمة

لا تضع قيم الأسرار داخل الملفات. هذه أسماء المتغيرات فقط:

```text
HALALSTREAM_MODAL_PURIFY_URL
HALALSTREAM_MODAL_SECRET
HALALSTREAM_ADMIN_USER
HALALSTREAM_ADMIN_PASSWORD
HALALSTREAM_MODAL_ESTIMATED_USD_PER_SECOND
HALALSTREAM_USAGE_TZ_OFFSET_HOURS
HALALSTREAM_MAX_ACTIVE_PROCESSING_JOBS
HALALSTREAM_MAX_LINK_DURATION_SECONDS
HALALSTREAM_PURIFICATION_DISABLED_MESSAGE
HALALSTREAM_NOADSDL_ENABLED
HALALSTREAM_COBALT_APIS
HALALSTREAM_YTDLP_PROXY
HALALSTREAM_YTDLP_COOKIES
```

الإعدادات الحالية المهمة من الكود:

- حد مدة الرابط: `600` ثانية، يعني 10 دقائق.
- التحميل المباشر مفعل افتراضياً: `HALALSTREAM_ALLOW_UNCHECKED_DIRECT=1`.
- NoAdsDL مفعل افتراضياً ليوتيوب.
- Tunelio غير مفعل افتراضياً.
- Modal timeout افتراضي: 1800 ثانية.
- تقدير تكلفة Modal الافتراضي: `0.00033` دولار/ثانية تقريباً.

## سلوك لا يجوز كسره

### 1. التحميل المباشر

إذا اختار المستخدم `purify_mode=direct`:

- لا تفحص الصوت.
- لا ترسل إلى Modal.
- لا تستخدم GPU.
- جهز الملف للتحميل كما هو.

هذا الخيار موجود لأن المستخدم أحياناً يريد التحميل فقط.

### 2. إيقاف التنقية من الأدمن

لوحة الأدمن:

```text
/admin/usage
```

وفي الدومين:

```text
https://halalstream.me/admin/usage/
```

تحتوي:

- إحصائيات استهلاك Modal تقديرية.
- زر إيقاف/تشغيل التنقية.

عند إيقاف التنقية:

- أي طلب تنقية جديد يرجع `503` برسالة عربية.
- التحميل المباشر يبقى شغال.
- الوظائف التي بدأت فعلاً قد تكمل؛ لا تعتمد على قتل job بدأ داخل Modal.

### 3. الروابط الطويلة

روابط التنقية لا يجب أن تتجاوز 10 دقائق حالياً حتى لا يصير طابور طويل وصرف عالي.

الدوال المهمة:

- `enforce_link_duration_seconds`
- `validate_link_media`
- `probe_media_duration_seconds`

### 4. Instagram/Reels

المشكلة التي حصلت: بعض روابط Instagram Reels أعادت من خادم التحميل ملف `jpg` بدل الفيديو، ثم ffmpeg فشل برسالة طويلة.

الإصلاح الحالي:

- `cobalt_download_candidates` يرتب النتائج حتى يختار فيديو/صوت قبل الصور.
- `validate_downloaded_media` يرفض الصور والملفات بلا صوت.
- `download_via_ytdlp` يستخدم كاحتياطي للروابط الخارجية إذا Cobalt فشل.
- `friendly_error` يحول أخطاء ffmpeg الطويلة إلى رسالة عربية قصيرة.

لا ترجع السلوك القديم الذي يختار أول عنصر من picker مباشرة.

### 5. التنقية والصوت الطبيعي

تمت عدة محاولات لتحسين العزل. القاعدة الحالية:

- لا تفرط في فلترة الصوت حتى لا يتغير صوت المتكلم أو المغني كثيراً.
- عند الشك في بقاء المعازف، fail safe أفضل من تمرير ملف سيئ.
- `modal_worker.py` و `server.py` يحتويان إعدادات RoFormer/UVR rescue وقياس residual music.

لا تغير عتبات التنقية الكبيرة إلا بعد اختبار ملفات حقيقية.

## نقاط API المهمة

```text
GET  /api/health
POST /api/jobs/link
POST /api/jobs/upload
GET  /api/jobs/{job_id}
POST /api/jobs/{job_id}/purify
POST /api/jobs/{job_id}/retry
GET  /api/jobs/{job_id}/download/{kind}
GET  /admin/usage
POST /admin/usage/purification
```

## تشغيل محلي على Windows

غالباً يكفي:

```powershell
.\run-local.bat
```

أو يدوياً:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn server:app --host 127.0.0.1 --port 8000 --reload
```

افتح:

```text
http://127.0.0.1:8000
```

لا تفتح `index.html` مباشرة إذا أردت معالجة حقيقية.

## فحوصات قبل النشر

```powershell
.\.venv\Scripts\python.exe -m py_compile server.py modal_worker.py
git diff --check -- server.py app.js
```

إذا Node متوفر:

```powershell
node --check app.js
```

اختبار health بعد النشر:

```powershell
Invoke-RestMethod -Uri "https://7haydar-halalstream.hf.space/api/health" -TimeoutSec 25
```

## آخر شغل مهم تم

آخر commits وقت كتابة هذا الملف:

```text
dbf595d Validate uploaded media before purification
88df3a9 Fix Instagram reel media validation
8d9da07 Add admin purification pause control
cbc134b Add admin usage redirect page
a2a9f0b Add private usage dashboard
6c28440 Stop link fallback after duration limit
2e6742b Limit link duration to ten minutes
935ff64 Add first-visit faith reminder
9616af6 Use free external YouTube download service
1ea9d12 Prefer working free Cobalt downloads
3146551 Add reliable authenticated YouTube downloads
26ed3ec Restore the previous fast download path
```

## حالة Git المحلية عند إنشاء هذا الملف

كان يوجد تعديل محلي قديم على:

```text
index.html
```

لا ترجعه ولا تلمسه إلا إذا طلب المستخدم ذلك أو كانت المهمة تتطلبه بوضوح. قبل أي عمل جديد شغل:

```powershell
git status --short
```

## أشياء لم تنفذ بعد ويمكن طلبها لاحقاً

- عداد زيارات داخلي بسيط يظهر في لوحة الأدمن، بدون IP كامل وبدون تتبع ثقيل.
- ربط Google Search Console للدومين. هذا مفيد للسيو ولا يتوقع أن يسبب مشكلة مع خوادم التحميل الخارجية لأنه يفحص الصفحات العامة فقط.
- Cloudflare Web Analytics إذا تم نقل DNS إلى Cloudflare.

## نصائح لكودكس الجديد

- اقرأ الكود قبل التعديل، ولا تفترض أن السلوك بسيط.
- أي تعديل في التحميل قد يؤثر على YouTube وInstagram وTikTok، فاختبر الرسائل والفشل الآمن.
- أي تعديل في التنقية قد يصرف Modal، فاختبر أولاً بملفات صغيرة أو فحوصات لا ترسل إلى Modal إن أمكن.
- لا تعرض للمستخدم logs طويلة أو أسرار أو stack traces.
- عند النشر انتظر Hugging Face حتى يلتقط commit الجديد؛ أحياناً يستغرق عدة دقائق.
- بعد اختبار زر إيقاف التنقية على الموقع الحي، أعد تركه مفعلاً إلا إذا طلب المستخدم إيقافه.

