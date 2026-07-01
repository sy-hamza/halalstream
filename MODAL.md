# تشغيل التنقية على Modal

هذا المسار يجعل Hugging Face مسؤولاً عن الواجهة والتنزيل فقط، بينما تعمل إزالة المعازف على Modal GPU عند الطلب. لا يتم استدعاء Modal في خيار التحميل المباشر.

## 1. تجهيز السر

اختر قيمة سرية قوية وضعها في Modal:

```bash
modal secret create halalstream-modal-secret HALALSTREAM_MODAL_SECRET=CHANGE_ME_TO_A_LONG_RANDOM_SECRET
```

ضع القيمة نفسها في أسرار Hugging Face Space باسم:

```text
HALALSTREAM_MODAL_SECRET
```

## 2. نشر عامل Modal

من مجلد المشروع:

```bash
python -m pip install modal
modal setup
modal deploy modal_worker.py
```

بعد النشر سيظهر رابط HTTPS للدالة. ضعه في أسرار Hugging Face Space باسم:

```text
HALALSTREAM_MODAL_PURIFY_URL
```

## 3. إعدادات اختيارية

```text
HALALSTREAM_MODAL_GPU=T4
HALALSTREAM_MODAL_PURIFY_TIMEOUT=1800
HALALSTREAM_MAX_ACTIVE_PROCESSING_JOBS=1
```

عند تفعيل `HALALSTREAM_MODAL_PURIFY_URL` و`HALALSTREAM_MODAL_SECRET` يصبح Demucs المحلي غير مطلوب لاعتبار الخادم جاهزاً، ويمكن تشغيل Hugging Face على CPU basic للتوفير.

## ملاحظات

- التحميل المباشر يبقى على Hugging Face فقط ولا يرسل شيئاً إلى Modal.
- عند التنقية يرسل Hugging Face مسار صوت FLAC نظيف إلى Modal بدل رفع الفيديو كاملاً، ثم يركب الصوت المنقى على الفيديو الأصلي.
- إذا لم تكن أسرار Modal مضبوطة، يعود الخادم تلقائياً إلى المسار المحلي القديم.
