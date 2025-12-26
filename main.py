from fastapi import FastAPI, UploadFile, File, Form, HTTPException, BackgroundTasks
import os
import shutil
import uuid
from supabase import create_client, Client
from dotenv import load_dotenv
from analyzer import FootballGameAnalyzer

load_dotenv()

app = FastAPI()

# Supabase Configuration
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if SUPABASE_URL and SUPABASE_KEY:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
else:
    print("WARNING: Supabase credentials not found.")

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
    user_id: str = Form(...) # Supabase User ID to save results to
):
    # 1. Save uploaded file temporarily
    temp_filename = f"upload_{uuid.uuid4()}.mp4"
    temp_path = os.path.join(os.getcwd(), temp_filename)
    
    try:
        with open(temp_path, "wb") as buffer:
            shutil.copyfileobj(video.file, buffer)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"File upload failed: {str(e)}")

    # 2. Run Analysis (This can take time, so we might want to do it in background)
    # For now, we await it to return the result directly. 
    # In a real async production, we'd use background_tasks and update Supabase later.
    
    try:
        analyzer = FootballGameAnalyzer()
        result = analyzer.analyze_game(temp_path, player_number, team, jersey_color)
        
        # Cleanup upload
        if os.path.exists(temp_path):
            os.remove(temp_path)

        if result["status"] == "success":
            # Save to Supabase
            if supabase:
                data = {
                    "user_id": user_id,
                    "player_number": player_number,
                    "team": team,
                    "analysis_text": result["analysis"],
                    "created_at": "now()"
                }
                # Insert into 'analyses' table (we need to create this table)
                try:
                    supabase.table("analyses").insert(data).execute()
                except Exception as e:
                    print(f"Failed to save to Supabase: {e}")

            return result
        else:
            raise HTTPException(status_code=500, detail=result.get("error"))

    except Exception as e:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise HTTPException(status_code=500, detail=str(e))
