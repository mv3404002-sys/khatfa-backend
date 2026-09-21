import asyncio
from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
import yt_dlp

app = FastAPI(title="خطفة - Backend")

# السماح لتطبيق أندرويد بالاتصال بالخادم من أي مكان
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def root():
    """نقطة تأكيد أن الخادم يعمل"""
    return {"status": "ok", "service": "khatfa-backend"}


@app.get("/info")
def get_info(url: str = Query(..., description="رابط الفيديو المراد جلب معلوماته")):
    """
    يستقبل رابط فيديو (يوتيوب/تيك توك/انستغرام/فيسبوك/تويتر)
    ويرجع معلوماته وقائمة الجودات المتاحة للتحميل.
    """
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"تعذر جلب معلومات الفيديو: {e}")

    formats = []
    for f in info.get("formats", []):
        has_video = f.get("vcodec") not in (None, "none")
        has_audio = f.get("acodec") not in (None, "none")

        # المرحلة الأولى: نكتفي بالصيغ الجاهزة (فيديو+صوت في ملف واحد)
        # لتجنب الحاجة لدمج الملفات على الخادم (يتطلب ffmpeg)
        if has_video and not has_audio:
            continue

        formats.append(
            {
                "format_id": f.get("format_id"),
                "ext": f.get("ext"),
                "resolution": f.get("format_note") or f.get("resolution") or "صوت فقط",
                "filesize": f.get("filesize") or f.get("filesize_approx"),
                "has_video": has_video,
                "has_audio": has_audio,
            }
        )

    return {
        "title": info.get("title"),
        "thumbnail": info.get("thumbnail"),
        "duration": info.get("duration"),
        "uploader": info.get("uploader"),
        "platform": info.get("extractor_key"),
        "formats": formats,
    }


@app.get("/download")
async def download(
    url: str = Query(..., description="رابط الفيديو"),
    format_id: str = Query("best", description="معرف الجودة من /info"),
):
    """
    يقوم بتحميل الفيديو فعليًا من المصدر وبثّه مباشرة لطالب الطلب
    (لا يُخزَّن أي ملف على الخادم).
    """
    cmd = [
        "yt-dlp",
        "-f", format_id,
        "-o", "-",
        "--no-playlist",
        "--quiet",
        "--no-warnings",
        url,
    ]

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    async def stream_generator():
        try:
            while True:
                chunk = await process.stdout.read(64 * 1024)
                if not chunk:
                    break
                yield chunk
        finally:
            await process.wait()

    return StreamingResponse(stream_generator(), media_type="video/mp4")
