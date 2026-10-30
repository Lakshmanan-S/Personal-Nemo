"""
Curated, zero-LLM-cost demo profile seed (Plan B: instant demo mode).


Hand-authored Facts + Traits matching the persona already implied by
sample_data.json (Japanese-food-and-ramen lover, hip-hop/R&B listener, gym +
weekend-run routine, a Goa trip, crime/thriller/sci-fi watcher, streetwear
style). Facts are inserted with privacy_tier left unset (None) - exactly as
real freshly-extracted facts look before the Super privacy tagger ever runs -
so that clicking "Rebuild profile" later with a real, funded provider key
genuinely re-tags and re-synthesizes these same facts via live Nemotron/OpenAI
calls, naturally replacing the seeded placeholder trait text with real AI
output. Traits are inserted fully populated so the profile and Know Me
Better screens look complete immediately, with zero API calls.


Each seed fact references the real `external_id`s already present in
sample_data.json (resolved to actual RawItem.id values at seed time in
app/services.py), so "Show evidence" on any trait displays genuine sample
emails/calendar events/tracks/activities/watch history - nothing about the
evidence-drilldown UX needs to change.
"""


# One entry per hand-authored Fact, mirroring what the real Nano extraction
# pipeline would produce: a short grounded statement plus the raw item(s)
# that support it. `fact_key` is a stable local id used only to wire facts to
# traits below (it is never stored in the DB).
DEMO_SEED_FACTS: list[dict] = [
    # ---- food ----
    {"fact_key": "food_sushi_yama_01", "category": "food", "statement": "Ordered from Sushi Yama (Japanese, Indiranagar) multiple times, including Salmon Sushi Platter and Dragon Roll.", "source_external_ids": ["gmail-001", "gmail-003", "gmail-007", "gmail-011"]},
    {"fact_key": "food_ramen_bar_01", "category": "food", "statement": "Repeat orders from Ramen Bar: Tonkotsu Ramen, Spicy Miso Ramen, Gyoza.", "source_external_ids": ["gmail-002", "gmail-008", "gmail-012"]},
    {"fact_key": "food_ikigai_01", "category": "food", "statement": "Frequent orders from Ikigai Ramen House: Shoyu Ramen, Karaage, Edamame.", "source_external_ids": ["gmail-005", "gmail-009", "gmail-013"]},
    {"fact_key": "food_ikigai_dinein_01", "category": "food", "statement": "Also dined in at Ikigai Ramen House in person, not just delivery.", "source_external_ids": ["gcal-009"]},
    {"fact_key": "food_other_cuisines_01", "category": "food", "statement": "Occasionally orders Thai (Naga Thai Kitchen) and North Indian (Punjab Grill) food.", "source_external_ids": ["gmail-004", "gmail-006", "gmail-010"]},


    # ---- music ----
    {"fact_key": "music_travis_scott_01", "category": "music", "statement": "Travis Scott is the top-played artist, with Sicko Mode, goosebumps and FEIN among frequently played tracks.", "source_external_ids": ["spotify-001", "spotify-004", "spotify-006", "spotify-011"]},
    {"fact_key": "music_kendrick_01", "category": "music", "statement": "Kendrick Lamar is a top artist, including conscious rap tracks HUMBLE. and Alright.", "source_external_ids": ["spotify-002", "spotify-007", "spotify-012"]},
    {"fact_key": "music_kanye_01", "category": "music", "statement": "Kanye West tracks played often, spanning hip hop and gospel rap (Flashing Lights, Praise God, POWER).", "source_external_ids": ["spotify-003", "spotify-010", "spotify-015"]},
    {"fact_key": "music_frank_ocean_01", "category": "music", "statement": "Frank Ocean's R&B tracks (Nights, Pink + White) are in regular rotation.", "source_external_ids": ["spotify-005", "spotify-008", "spotify-013"]},
    {"fact_key": "music_tame_impala_01", "category": "music", "statement": "Tame Impala's psychedelic rock also shows up in recent listening (The Less I Know the Better).", "source_external_ids": ["spotify-009", "spotify-014"]},


    # ---- entertainment ----
    {"fact_key": "ent_crime_shows_01", "category": "entertainment", "statement": "Watched and highly rated crime/thriller shows Breaking Bad and Better Call Saul.", "source_external_ids": ["trakt-001", "trakt-002"]},
    {"fact_key": "ent_scifi_thriller_01", "category": "entertainment", "statement": "Watched sci-fi/mystery shows Dark and Severance, both rated 9-10.", "source_external_ids": ["trakt-003", "trakt-004"]},
    {"fact_key": "ent_movies_01", "category": "entertainment", "statement": "Watched thriller/mystery films Oppenheimer, The Prestige and Shutter Island.", "source_external_ids": ["trakt-005", "trakt-006", "trakt-010"]},
    {"fact_key": "ent_crime_shows_02", "category": "entertainment", "statement": "Also watched crime dramas True Detective, Mindhunter and Fargo.", "source_external_ids": ["trakt-007", "trakt-009", "trakt-012"]},
    {"fact_key": "ent_scifi_movie_01", "category": "entertainment", "statement": "Watched the sci-fi adventure film Dune: Part Two.", "source_external_ids": ["trakt-008"]},
    {"fact_key": "ent_comedy_outlier_01", "category": "entertainment", "statement": "Occasionally watches lighter comedy-drama, such as The Bear.", "source_external_ids": ["trakt-011"]},


    # ---- fitness ----
    {"fact_key": "fitness_strength_01", "category": "fitness", "statement": "Attends Cult.fit Indiranagar for a leg/push/pull strength-training split roughly 3x per week, early mornings.", "source_external_ids": ["gcal-001", "gcal-002", "gcal-003", "gcal-004", "gcal-005", "gcal-006", "gcal-010", "gcal-014"]},
    {"fact_key": "fitness_strength_strava_01", "category": "fitness", "statement": "Strava confirms the same weekday strength-training pattern at Cult.fit Indiranagar.", "source_external_ids": ["strava-001", "strava-002", "strava-004", "strava-006", "strava-008", "strava-011"]},
    {"fact_key": "fitness_running_01", "category": "fitness", "statement": "Runs at Cubbon Park on weekends, with distances increasing from ~8km to 14-15km long runs.", "source_external_ids": ["gcal-011", "gcal-015", "strava-003", "strava-005", "strava-007", "strava-009", "strava-012"]},


    # ---- routine ----
    {"fact_key": "routine_morning_01", "category": "routine", "statement": "Consistent early-morning schedule: gym or run around 6:15-7:00 AM on weekdays before work.", "source_external_ids": ["gcal-001", "gcal-003", "gcal-005", "gcal-014"]},
    {"fact_key": "routine_work_01", "category": "routine", "statement": "Regular recurring work meetings: Sprint Planning and Daily Standup around 9:30 AM.", "source_external_ids": ["gcal-007", "gcal-008"]},
    {"fact_key": "routine_commute_01", "category": "routine", "statement": "Frequently commutes by Uber between Indiranagar and Koramangala in the evening.", "source_external_ids": ["gmail-017", "gmail-018"]},


    # ---- places_travel ----
    {"fact_key": "travel_goa_01", "category": "places_travel", "statement": "Took a flight (IndiGo, BLR to GOI) and booked a sea-view suite at Azul Beach Resort for a Goa trip.", "source_external_ids": ["gmail-019", "gmail-020"]},
    {"fact_key": "travel_goa_02", "category": "places_travel", "statement": "Calendar confirms a multi-day 'Goa Getaway' trip in early October.", "source_external_ids": ["gcal-012"]},
    {"fact_key": "travel_goa_run_01", "category": "places_travel", "statement": "Went for a beach run at Baga Beach, Goa during the trip.", "source_external_ids": ["strava-010"]},
    {"fact_key": "travel_open_evening_01", "category": "places_travel", "statement": "Has occasional free evenings with no plans scheduled, open to spontaneous suggestions.", "source_external_ids": ["gcal-013"]},


    # ---- style_shopping ----
    {"fact_key": "style_sneakers_01", "category": "style_shopping", "statement": "Bought Nike Air Max and Nike Dunk Low sneakers, plus an oversized streetwear hoodie.", "source_external_ids": ["gmail-014", "gmail-015"]},
    {"fact_key": "style_streetwear_01", "category": "style_shopping", "statement": "Also bought Adidas Ultraboost sneakers and a streetwear bomber jacket.", "source_external_ids": ["gmail-016"]},


    # ---- personality ----
    {"fact_key": "personality_disciplined_01", "category": "personality", "statement": "Maintains a highly disciplined weekday routine (early gym sessions, consistent work schedule) while still carving out time for spontaneous trips.", "source_external_ids": ["gcal-001", "gcal-012", "gcal-013"]},
    {"fact_key": "personality_taste_driven_01", "category": "personality", "statement": "Shows strong, consistent taste preferences across domains (same few restaurants, same few artists, same genre of shows) rather than broad experimentation.", "source_external_ids": ["gmail-001", "spotify-001", "trakt-001"]},
]


# One entry per synthesized Trait (mirrors Ultra synthesis output). `fact_keys`
# reference the `fact_key`s above; confidence/summary are authored to read
# exactly like real synthesis output (e.g. "Based on N food orders...").
DEMO_SEED_TRAITS: list[dict] = [
    {
        "category": "food", "key": "favorite_cuisine",
        "value": "Japanese food, especially ramen and sushi",
        "summary": "Based on 10 food orders, mostly from Sushi Yama, Ramen Bar and Ikigai Ramen House.",
        "confidence": 0.92,
        "fact_keys": ["food_sushi_yama_01", "food_ramen_bar_01", "food_ikigai_01", "food_ikigai_dinein_01"],
    },
    {
        "category": "food", "key": "occasional_cuisines",
        "value": "Occasionally orders Thai and North Indian food",
        "summary": "Based on 3 orders outside the usual Japanese favorites.",
        "confidence": 0.55,
        "fact_keys": ["food_other_cuisines_01"],
    },
    {
        "category": "music", "key": "favorite_genre",
        "value": "Hip-hop and trap, with R&B and psychedelic rock in the mix",
        "summary": "Based on top artists and recently played tracks: Travis Scott, Kendrick Lamar, Kanye West, Frank Ocean and Tame Impala.",
        "confidence": 0.9,
        "fact_keys": ["music_travis_scott_01", "music_kendrick_01", "music_kanye_01", "music_frank_ocean_01", "music_tame_impala_01"],
    },
    {
        "category": "entertainment", "key": "preferred_genres",
        "value": "Crime, thriller and sci-fi TV shows and movies",
        "summary": "Based on 10 highly-rated watches including Breaking Bad, Severance, Oppenheimer and Dune: Part Two.",
        "confidence": 0.88,
        "fact_keys": ["ent_crime_shows_01", "ent_scifi_thriller_01", "ent_movies_01", "ent_crime_shows_02", "ent_scifi_movie_01"],
    },
    {
        "category": "entertainment", "key": "occasional_comedy",
        "value": "Occasionally watches lighter comedy-drama",
        "summary": "Based on 1 watch (The Bear) outside the usual crime/thriller taste.",
        "confidence": 0.35,
        "fact_keys": ["ent_comedy_outlier_01"],
    },
    {
        "category": "fitness", "key": "workout_routine",
        "value": "Strength training ~3x/week (leg/push/pull split) plus weekend long runs",
        "summary": "Based on 15 gym and run sessions across calendar and Strava.",
        "confidence": 0.93,
        "fact_keys": ["fitness_strength_01", "fitness_strength_strava_01", "fitness_running_01"],
    },
    {
        "category": "routine", "key": "morning_routine",
        "value": "Early-morning gym or run before work, with regular 9:30 AM work meetings",
        "summary": "Based on a consistent weekday calendar pattern.",
        "confidence": 0.8,
        "fact_keys": ["routine_morning_01", "routine_work_01"],
    },
    {
        "category": "routine", "key": "evening_commute",
        "value": "Frequently commutes between Indiranagar and Koramangala in the evening",
        "summary": "Based on 2 Uber trip receipts.",
        "confidence": 0.5,
        "fact_keys": ["routine_commute_01"],
    },
    {
        "category": "places_travel", "key": "recent_trip",
        "value": "Took a long-weekend trip to Goa in early October, including a beach run",
        "summary": "Based on flight and hotel bookings, a calendar trip event, and a Strava activity in Goa.",
        "confidence": 0.85,
        "fact_keys": ["travel_goa_01", "travel_goa_02", "travel_goa_run_01"],
    },
    {
        "category": "places_travel", "key": "open_to_spontaneity",
        "value": "Sometimes has free evenings with no plans",
        "summary": "Based on 1 calendar entry marked as an open evening.",
        "confidence": 0.4,
        "fact_keys": ["travel_open_evening_01"],
    },
    {
        "category": "style_shopping", "key": "style_profile",
        "value": "Streetwear style - sneakers (Nike, Adidas), hoodies and bomber jackets",
        "summary": "Based on 3 clothing and footwear orders.",
        "confidence": 0.82,
        "fact_keys": ["style_sneakers_01", "style_streetwear_01"],
    },
    {
        "category": "personality", "key": "disciplined_but_spontaneous",
        "value": "Disciplined weekday routine balanced with spontaneous weekend trips",
        "summary": "Based on the contrast between a consistent gym/work schedule and an unplanned getaway.",
        "confidence": 0.6,
        "fact_keys": ["personality_disciplined_01"],
    },
    {
        "category": "personality", "key": "consistent_taste",
        "value": "Strong, consistent taste preferences rather than broad experimentation",
        "summary": "Based on repeat patterns across food, music and entertainment choices.",
        "confidence": 0.55,
        "fact_keys": ["personality_taste_driven_01"],
    },
]



