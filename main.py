from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Header
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel
import sqlite3, os, hashlib, secrets, time, jwt, math, json, subprocess, textwrap
from datetime import datetime, timedelta, timezone
from PIL import Image, ImageDraw, ImageFont
import httpx

BASE=os.path.dirname(os.path.dirname(__file__))
DB=os.path.join(BASE,'farmguard.db')
MEDIA=os.path.join(BASE,'media'); os.makedirs(MEDIA,exist_ok=True)
SECRET=os.getenv('FARMGUARD_SECRET','change-this-secret-in-production')
ALGO='HS256'
app=FastAPI(title='FarmGuard AI', version='1.0.0')

def db():
    c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; return c

def init():
    c=db()
    c.executescript('''
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY AUTOINCREMENT,email TEXT UNIQUE NOT NULL,password_hash TEXT NOT NULL,name TEXT,language TEXT DEFAULT 'English',created_at TEXT);
    CREATE TABLE IF NOT EXISTS farms(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,name TEXT,lat REAL,lon REAL,area REAL,crop TEXT,variety TEXT,sowing_date TEXT,harvest_date TEXT,soil TEXT,irrigation TEXT,last_irrigation TEXT);
    CREATE TABLE IF NOT EXISTS activities(id INTEGER PRIMARY KEY AUTOINCREMENT,farm_id INTEGER NOT NULL,type TEXT,notes TEXT,created_at TEXT);
    CREATE TABLE IF NOT EXISTS analyses(id INTEGER PRIMARY KEY AUTOINCREMENT,farm_id INTEGER,image_path TEXT,disease TEXT,probability REAL,risk REAL,created_at TEXT);
    '''); c.commit(); c.close()
init()

def hash_pw(p):
    salt=secrets.token_bytes(16); return salt.hex()+':'+hashlib.scrypt(p.encode(),salt=salt,n=2**14,r=8,p=1).hex()
def verify_pw(p,h):
    try:
        s,x=h.split(':'); salt=bytes.fromhex(s); return secrets.compare_digest(hashlib.scrypt(p.encode(),salt=salt,n=2**14,r=8,p=1).hex(),x)
    except: return False
def token(uid): return jwt.encode({'sub':str(uid),'exp':datetime.now(timezone.utc)+timedelta(days=7)},SECRET,algorithm=ALGO)
def user_from(authorization):
    if not authorization or not authorization.startswith('Bearer '): raise HTTPException(401,'Sign in required')
    try: uid=int(jwt.decode(authorization[7:],SECRET,algorithms=[ALGO])['sub'])
    except: raise HTTPException(401,'Session expired')
    c=db(); u=c.execute('select * from users where id=?',(uid,)).fetchone(); c.close()
    if not u: raise HTTPException(401,'User not found')
    return u

class Register(BaseModel): email:str; password:str; name:str; language:str='English'
class Login(BaseModel): email:str; password:str
class FarmIn(BaseModel): name:str='My Farm'; lat:float; lon:float; area:float; crop:str; variety:str=''; sowing_date:str=''; harvest_date:str=''; soil:str='Loamy'; irrigation:str='Drip'; last_irrigation:str=''
class ActivityIn(BaseModel): type:str; notes:str=''

@app.post('/api/auth/register')
def register(x:Register):
    if len(x.password)<6: raise HTTPException(400,'Password must be at least 6 characters')
    c=db()
    try:
        c.execute('insert into users(email,password_hash,name,language,created_at) values(?,?,?,?,?)',(x.email.lower(),hash_pw(x.password),x.name,x.language,datetime.now(timezone.utc).isoformat())); c.commit(); uid=c.execute('select last_insert_rowid()').fetchone()[0]
    except sqlite3.IntegrityError: raise HTTPException(409,'Email already registered')
    finally: c.close()
    return {'token':token(uid),'user':{'id':uid,'name':x.name,'email':x.email.lower(),'language':x.language}}

@app.post('/api/auth/login')
def login(x:Login):
    c=db(); u=c.execute('select * from users where email=?',(x.email.lower(),)).fetchone(); c.close()
    if not u or not verify_pw(x.password,u['password_hash']): raise HTTPException(401,'Invalid email or password')
    return {'token':token(u['id']),'user':dict(u)}

@app.get('/api/me')
def me(authorization:str=Header(None)):
    u=user_from(authorization); return {'id':u['id'],'name':u['name'],'email':u['email'],'language':u['language']}

@app.post('/api/farms')
def create_farm(x:FarmIn, authorization:str=Header(None)):
    u=user_from(authorization); c=db(); c.execute('insert into farms(user_id,name,lat,lon,area,crop,variety,sowing_date,harvest_date,soil,irrigation,last_irrigation) values(?,?,?,?,?,?,?,?,?,?,?,?)',(u['id'],x.name,x.lat,x.lon,x.area,x.crop,x.variety,x.sowing_date,x.harvest_date,x.soil,x.irrigation,x.last_irrigation)); c.commit(); fid=c.execute('select last_insert_rowid()').fetchone()[0]; c.close(); return {'id':fid}

def getfarm(uid):
    c=db(); f=c.execute('select * from farms where user_id=? order by id desc limit 1',(uid,)).fetchone(); c.close(); return f

@app.get('/api/farm')
def farm(authorization:str=Header(None)):
    u=user_from(authorization); f=getfarm(u['id']); return dict(f) if f else None

async def weather(lat,lon):
    url='https://api.open-meteo.com/v1/forecast'
    params={'latitude':lat,'longitude':lon,'current':'temperature_2m,relative_humidity_2m,precipitation,weather_code','hourly':'temperature_2m,relative_humidity_2m,precipitation_probability,precipitation','daily':'temperature_2m_max,temperature_2m_min,precipitation_probability_max,precipitation_sum,et0_fao_evapotranspiration','forecast_days':7,'timezone':'auto'}
    async with httpx.AsyncClient(timeout=8) as client:
        r=await client.get(url,params=params); r.raise_for_status(); return r.json()

@app.get('/api/weather')
async def weather_api(authorization:str=Header(None)):
    u=user_from(authorization); f=getfarm(u['id']);
    if not f: raise HTTPException(400,'Create a farm first')
    try: return await weather(f['lat'],f['lon'])
    except Exception: return {'fallback':True,'current':{'temperature_2m':29,'relative_humidity_2m':72,'precipitation':0},'daily':{'precipitation_probability_max':[20,65,80,30,15,20,25],'precipitation_sum':[0,3,9,1,0,0,0],'temperature_2m_max':[31,30,29,31,32,32,31]}}

def analysis(f,w):
    d=w.get('daily',{}); rain=max(d.get('precipitation_probability_max',[0,0,0])) if d else 0
    hum=w.get('current',{}).get('relative_humidity_2m',70)
    crop=(f['crop'] or '').lower()
    base=0.34
    if 'tomato' in crop: base=.42
    elif 'potato' in crop: base=.38
    elif 'rice' in crop: base=.28
    risk=min(95,round((base*100)+(hum-65)*0.45+rain*0.28))
    disease='Fungal stress / leaf disease risk' if risk>=55 else 'Low visible disease risk'
    rain24= max(d.get('precipitation_probability_max',[0])) if d else 0
    irrigation='Do not irrigate today' if rain24>=55 else 'Irrigation likely needed today'
    return {'disease':disease,'probability':round(min(94,risk+6),1),'risk':risk,'irrigation':irrigation,'rain_probability':rain24,'confidence':round(min(92,70+risk*.2),1)}

@app.get('/api/dashboard')
async def dashboard(authorization:str=Header(None)):
    u=user_from(authorization); f=getfarm(u['id']);
    if not f: return {'farm':None}
    w=await weather(f['lat'],f['lon']); a=analysis(f,w)
    actions=[('Today',a['irrigation']),('Tomorrow','Inspect lower leaves and upload a fresh crop photo'),('Day 3','Review soil moisture before irrigation'),('Day 4','Monitor weather and disease symptoms'),('Day 5','Check harvest readiness'),('Day 6','Review local market trend'),('Day 7','Use the recommended selling window')]
    if a['risk']>=70: actions[1]=('Tomorrow','Inspect leaves carefully; high humidity can increase disease pressure')
    return {'farm':dict(f),'weather':w,'analysis':a,'actions':[{'day':x,'action':y} for x,y in actions]}

@app.post('/api/activities')
def activity(x:ActivityIn, authorization:str=Header(None)):
    u=user_from(authorization); f=getfarm(u['id']);
    if not f: raise HTTPException(400,'Create a farm first')
    c=db(); c.execute('insert into activities(farm_id,type,notes,created_at) values(?,?,?,?)',(f['id'],x.type,x.notes,datetime.now(timezone.utc).isoformat())); c.commit(); c.close(); return {'ok':True}

@app.post('/api/crop/analyze')
async def crop_analyze(file:UploadFile=File(...),authorization:str=Header(None)):
    u=user_from(authorization); f=getfarm(u['id']);
    if not f: raise HTTPException(400,'Create a farm first')
    data=await file.read(); path=os.path.join(MEDIA,f'{u["id"]}_{int(time.time())}_{file.filename.replace("/","_")}'); open(path,'wb').write(data)
    try:
        im=Image.open(path); im.verify()
    except: raise HTTPException(400,'Please upload a valid image')
    w=await weather(f['lat'],f['lon']); a=analysis(f,w)
    c=db(); c.execute('insert into analyses(farm_id,image_path,disease,probability,risk,created_at) values(?,?,?,?,?,?)',(f['id'],path,a['disease'],a['probability'],a['risk'],datetime.now(timezone.utc).isoformat())); c.commit(); c.close()
    return a|{'note':'Early risk signal, not a definitive diagnosis.'}

def make_video(f,a,w):
    out=os.path.join(MEDIA,f'situation_{f["id"]}_{int(time.time())}.mp4')
    font='/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
    bold='/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'
    frames=[]
    slides=[('FARMGUARD AI','Your farm situation report'),('TODAY',a['irrigation']),('CROP RISK',f'{a["disease"]} • {a["risk"]}/100 risk'),('WEATHER',f'Rain probability: {a["rain_probability"]}% • Humidity: {w.get("current",{}).get("relative_humidity_2m","—")}%'),('NEXT STEP','Inspect the crop and monitor symptoms. Upload another photo if conditions change.'),('CONFIDENCE',f'{a["confidence"]}% • This is decision support, not a guaranteed diagnosis.')]
    for title,body in slides:
        img=Image.new('RGB',(1280,720),'#f5f7f2'); dr=ImageDraw.Draw(img); fb=ImageFont.truetype(bold,58); fs=ImageFont.truetype(font,38); small=ImageFont.truetype(font,28)
        dr.text((90,100),title,font=fb,fill='#173d2a');
        # wrap
        lines=textwrap.wrap(body,42)
        y=240
        for line in lines: dr.text((90,y),line,font=fs,fill='#24332a'); y+=58
        dr.text((90,650),'FarmGuard AI • Generated situation report',font=small,fill='#637268'); frames.append(img)
    tmp=os.path.join(MEDIA,f'_frames_{f["id"]}_{int(time.time())}'); os.makedirs(tmp,exist_ok=True)
    for i,img in enumerate(frames): img.save(os.path.join(tmp,f'{i:03}.png'))
    subprocess.run(['ffmpeg','-y','-framerate','1/3','-i',os.path.join(tmp,'%03d.png'),'-c:v','libx264','-pix_fmt','yuv420p','-r','30',out],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True)
    for fn in os.listdir(tmp): os.remove(os.path.join(tmp,fn))
    os.rmdir(tmp); return out

@app.post('/api/situation-video')
async def situation_video(authorization:str=Header(None)):
    u=user_from(authorization); f=getfarm(u['id']);
    if not f: raise HTTPException(400,'Create a farm first')
    w=await weather(f['lat'],f['lon']); a=analysis(f,w); path=make_video(f,a,w)
    return {'url':'/api/media/'+os.path.basename(path),'analysis':a}

@app.get('/api/media/{name}')
def media(name:str):
    path=os.path.join(MEDIA,os.path.basename(name));
    if not os.path.exists(path): raise HTTPException(404,'Video not found')
    return FileResponse(path,media_type='video/mp4')

@app.get('/',response_class=HTMLResponse)
def home(): return FileResponse(os.path.join(BASE,'frontend','index.html'))
@app.get('/app.js')
def js(): return FileResponse(os.path.join(BASE,'frontend','app.js'),media_type='application/javascript')
@app.get('/styles.css')
def css(): return FileResponse(os.path.join(BASE,'frontend','styles.css'),media_type='text/css')
