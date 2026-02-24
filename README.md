# ⚾ BaseballIQ — Backend API

FastAPI backend for the BaseballIQ props analysis app.
Phase 1 MVP: Home Run props powered by live MLB, Statcast, Odds, and Weather data.

---

## 📁 Project Structure

```
baseballiq/
├── main.py                  ← FastAPI app entry point
├── config.py                ← Environment variable config
├── requirements.txt         ← Python dependencies
├── .env.example             ← Copy to .env and fill in keys
│
├── routes/
│   ├── props.py             ← GET /api/props (main endpoint)
│   └── games.py             ← GET /api/games
│
├── services/
│   ├── mlb.py               ← MLB Stats API (free, no key)
│   ├── statcast.py          ← Baseball Savant via pybaseball
│   ├── odds.py              ← The Odds API (paid key required)
│   └── weather.py           ← Tomorrow.io weather API
│
└── models/
    ├── scoring.py           ← Confidence scoring engine (the algorithm)
    └── park_factors.py      ← Static HR factors for all 30 parks
```

---

## 🚀 Local Setup (Step by Step)

### 1. Prerequisites
- Python 3.11+
- pip

### 2. Clone and install
```bash
# Navigate to the project folder
cd baseballiq

# Create a virtual environment
python -m venv venv
source venv/bin/activate        # Mac/Linux
# venv\Scripts\activate         # Windows

# Install dependencies
pip install -r requirements.txt
```

### 3. Set up environment variables
```bash
cp .env.example .env
# Open .env and fill in your API keys (see "API Keys" section below)
```

### 4. Run the server
```bash
python main.py
# OR
uvicorn main:app --reload --port 8000
```

### 5. Test it
Open your browser to:
- http://localhost:8000/docs        ← Interactive API docs (Swagger UI)
- http://localhost:8000/health      ← Health check
- http://localhost:8000/api/games   ← Today's MLB schedule
- http://localhost:8000/api/props   ← All props (requires API keys)

---

## 🔑 API Keys You Need

### The Odds API (Required for odds/lines)
1. Go to https://the-odds-api.com
2. Sign up for free account (500 req/month free)
3. Copy your API key into `.env` as `ODDS_API_KEY`

### Tomorrow.io (Required for weather)
1. Go to https://tomorrow.io
2. Sign up for free account
3. Copy your API key into `.env` as `WEATHER_API_KEY`

### MLB Stats API
- **No key needed** — it's completely free and public

### Baseball Savant / pybaseball
- **No key needed** — pybaseball scrapes public data
- Note: first run is slow (~30s) while it downloads data; subsequent calls use cache

---

## 📡 API Endpoints

### `GET /api/props`
Returns all of today's player props ranked by confidence.

**Query params:**
| Param | Values | Default |
|-------|--------|---------|
| `sort_by` | `confidence`, `edge` | `confidence` |
| `min_grade` | `all`, `a`, `b`, `c` | `all` |
| `prop_type` | `Home Run`, `Hit`, etc. | all types |

**Example response:**
```json
{
  "props": [
    {
      "player_name": "Aaron Judge",
      "team": "NYY",
      "prop_type": "Home Run",
      "confidence": 87.2,
      "grade": "A",
      "grade_desc": "Excellent",
      "model_prob": "38.1%",
      "implied_prob": "23.8%",
      "edge_str": "+14.3%",
      "over_odds": "+320",
      "category_scores": {
        "hitter": 84.2,
        "pitcher": 71.3,
        "park": 55.0,
        "weather": 88.4,
        "situational": 72.1
      }
    }
  ],
  "count": 24,
  "generated_at": "2024-04-15T18:30:00"
}
```

### `GET /api/props/{type}`
Props for a single type. Types: `home-run`, `hit`, `stolen-base`, `strikeout`, `rbi`

### `GET /api/games`
Today's MLB schedule with probable pitchers.

### `POST /api/props/refresh`
Manually trigger a fresh data pull (runs in background).

---

## 🔗 Connecting to the React Frontend

In your React app, replace the mock `allProps` array with:

```javascript
// In your App component:
const [props, setProps] = useState([]);
const [loading, setLoading] = useState(true);

useEffect(() => {
  fetch("http://localhost:8000/api/props")
    .then(res => res.json())
    .then(data => {
      setProps(data.props);
      setLoading(false);
    });
}, []);
```

---

## ☁️ Deploying to Railway (Production)

1. Push your code to GitHub
2. Go to https://railway.app → New Project → Deploy from GitHub
3. Select your repo
4. Add environment variables (same as your `.env`) in Railway dashboard
5. Railway auto-detects Python and runs `uvicorn main:app`
6. Your API is live at `https://your-app.railway.app`

Update your React frontend's fetch URL to point to your Railway URL.

---

## 🛠️ Phase 2 Additions (Next Steps)
- [ ] PostgreSQL via Supabase (store results, track accuracy)
- [ ] Redis cache (faster than in-memory, survives restarts)
- [ ] APScheduler (auto-refresh props every 5 minutes)
- [ ] Hit, SB, Strikeout, RBI prop scoring
- [ ] Platoon splits integration
- [ ] User bet tracker endpoints
