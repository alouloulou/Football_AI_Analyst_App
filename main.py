from fastapi import FastAPI, UploadFile, File, Form, HTTPException, BackgroundTasks
import os
import shutil
import uuid
from dotenv import load_dotenv
from analyzer import FootballGameAnalyzer

load_dotenv()

app = FastAPI()

@app.get("/")
def read_root():
    return {"status": "online", "service": "Football Analysis Backend"}

@app.post("/analyze")
async def analyze_video(
    background_tasks: BackgroundTasks,
    video: UploadFile = File(...),
    player_number: str = Form(...),
    team: str = Form(...),
    jersey_color: str = Form(...),
    user_id: str = Form(...),
    user_notes: str = Form(None)  # Optional user context
):
    # 1. Save uploaded file temporarily
    temp_filename = f"upload_{uuid.uuid4()}.mp4"
    temp_path = os.path.join(os.getcwd(), temp_filename)
    
    try:
        with open(temp_path, "wb") as buffer:
            shutil.copyfileobj(video.file, buffer)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"File upload failed: {str(e)}")

    # 2. Run Analysis
    try:
        analyzer = FootballGameAnalyzer()
        result = analyzer.analyze_game(temp_path, player_number, team, jersey_color, user_notes)
        
        # Cleanup upload
        if os.path.exists(temp_path):
            os.remove(temp_path)

        if result["status"] == "success":
            # Flutter app handles saving to Supabase
            return result
        else:
            raise HTTPException(status_code=500, detail=result.get("error"))

    except Exception as e:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise HTTPException(status_code=500, detail=str(e))

