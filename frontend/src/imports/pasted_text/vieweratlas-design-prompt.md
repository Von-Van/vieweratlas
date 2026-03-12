Now I have a solid understanding of ViewerAtlas. Here's a comprehensive Figma design prompt you can use:

---

## ViewerAtlas — Figma Design Prompt

**Project:** ViewerAtlas — Twitch Community Intelligence Dashboard

---

### Context

ViewerAtlas is a data analytics tool that maps Twitch communities by tracking shared viewers between channels. It collects live chat data, builds weighted overlap graphs, runs community detection (Louvain algorithm), and produces visualizations showing which Twitch channels share audiences.

---

### Design Brief

Create a **web dashboard** for ViewerAtlas with the following pages/views:

---

#### 1. Landing / Hero Page
- Name: **ViewerAtlas**
- Tagline: *"Map the Twitch universe. Discover hidden communities."*
- Primary CTA: "Explore the Map" / "View Latest Analysis"
- Show a teaser of the network graph visualization
- Aesthetic: dark theme, data-forward, Twitch-inspired purple accents with cyan/teal highlights

---

#### 2. Community Map (Main View)
- Full-page **interactive network graph** (nodes = Twitch channels, edges = shared viewers)
- Node size = viewer count; edge thickness = shared viewer overlap weight
- Color-coded by detected community (game genre / language region)
- Sidebar panel with:
  - Community legend (color → label, e.g. "FPS English", "Variety Japanese")
  - Top channels list per community
  - Node detail drawer on click: channel name, game, viewer count, top overlapping channels
- Filters: by game category, language, minimum viewers, date range

---

#### 3. Channel Detail Page
- Channel header: avatar, name, game, viewer count
- **Overlap chart**: bar chart of top 10 most-overlapping channels and shared viewer counts
- **Community membership**: which community this channel belongs to, modularity score
- **Timeline**: viewer count trend over collection period
- Related channels grid

---

#### 4. Stats / Overview Dashboard
- Key metrics cards:
  - Total channels tracked
  - Total unique viewers observed
  - Number of communities detected
  - Modularity score (graph quality metric)
  - Data collection period
- Data source breakdown (live vs VOD)
- Top communities by size (bar chart)
- Most connected channels (top 10 by edge count)

---

#### 5. About / How It Works Page
- 3-step pipeline explainer with icons:
  1. **Collect** — Live Twitch chat sampling every hour
  2. **Analyze** — Build viewer overlap graph + run community detection
  3. **Visualize** — Explore the Twitch community map
- Data policy summary (privacy-conscious, no personal data stored)

---

### Design Tokens

| Token | Value |
|---|---|
| Background | `#0E0E10` (Twitch dark) |
| Surface | `#18181B` |
| Border | `#2A2A2E` |
| Primary accent | `#9147FF` (Twitch purple) |
| Secondary accent | `#00E5CC` (cyan) |
| Text primary | `#EFEFF1` |
| Text muted | `#848494` |
| Success | `#1DB954` |
| Warning | `#FF7B00` |
| Font | Inter or Space Grotesk |

---

### Key UX Requirements
- Mobile-responsive layout (focus on desktop, secondary mobile)
- Graph view should feel like an explorable map (zoom/pan interactions — annotate with interaction notes)
- Loading states for data-heavy views
- Empty state designs for when no data is collected yet
- Dark mode only

---

### Deliverables to Design in Figma
1. Component library (buttons, cards, badges, data cells, nav)
2. All 5 pages in desktop (1440px) and mobile (375px)
3. Interactive prototype flow: Landing → Map → Channel Detail
4. Annotation layer for developer handoff

---

You can paste this directly into a Figma AI prompt, a design brief doc, or hand it to a designer. Want me to refine any section or tailor it toward a specific use case (e.g. public-facing site vs internal tool)?