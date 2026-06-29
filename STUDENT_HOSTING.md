# تشغيل HalalStream عبر GitHub Student

Hugging Face مناسب كتجربة، لكنه ليس مستقراً لتنزيل روابط YouTube. الحل الأفضل المجاني أو شبه المجاني عبر GitHub Student هو تشغيل المشروع على VPS حقيقي.

## الخيار المفضل: DigitalOcean Student Credit

إذا ظهر لك عرض DigitalOcean داخل GitHub Student Developer Pack، فهذا أسهل خيار:

- أنشئ Droplet بنظام Ubuntu.
- اختر 2 vCPU و 4GB RAM كبداية. إذا كانت المقاطع طويلة اختر 8GB RAM.
- افتح منفذ `8000` مؤقتاً للتجربة.
- شغّل المشروع عبر Docker Compose.

أوامر الخادم:

```bash
sudo apt update
sudo apt install -y git docker.io docker-compose-plugin
sudo systemctl enable --now docker
git clone https://github.com/sy-hamza/halalstream.git
cd halalstream
mkdir -p secrets
sudo docker compose up -d --build
```

بعدها افتح:

```text
http://SERVER_IP:8000
```

## خيار جيد: Azure for Students

Azure for Students يعطي رصيداً مجانياً للطلاب. اختر Virtual Machine بنظام Ubuntu، ثم نفّذ نفس أوامر Docker أعلاه.

## لماذا VPS يحل مشكلة YouTube؟

- الخادم يكون دائم وليس Space مؤقتاً.
- الشبكة غالباً أثبت من Hugging Face مع yt-dlp.
- يمكن إضافة `cookies.txt` عند الحاجة إذا طلب YouTube تسجيل دخول.
- يمكن ربط دومين حقيقي لاحقاً عبر Cloudflare أو Nginx.

## دعم cookies عند الحاجة

إذا منع YouTube بعض الروابط:

1. صدّر cookies من المتصفح بصيغة Netscape cookies.txt.
2. ارفع الملف إلى:

```text
secrets/cookies.txt
```

3. فعّل هذا السطر داخل `docker-compose.yml`:

```yaml
HALALSTREAM_YTDLP_COOKIES: /home/user/app/secrets/cookies.txt
```

4. أعد تشغيل الخادم:

```bash
sudo docker compose up -d --build
```

## بعد نجاح الخادم

يمكن ربطه بواجهة رسمية على Netlify أو Cloudflare Pages، أو ربط دومين مباشرة بالخادم.
