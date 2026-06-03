# GSA Gateway Bot — Features & Commands

## Q: What is GSA Gateway?
**A:** GSA Gateway is the official Discord bot for NJIT's Graduate Student Association. It answers questions about GSA events, resources, and services; lets students submit initiatives and feedback; and runs daily enrichment content like MathCafe. You can talk to it freely in #ask-gsa or use slash commands anywhere in the server.

## Q: What commands does the GSA Gateway bot have?
**A:** GSA Gateway has the following slash commands:
- `/ask` — search the GSA knowledge base for FAQs, policies, and resources
- `/events` — list all upcoming GSA events
- `/event [name]` — get full details for a specific event
- `/resources [category]` — browse resources by category (funding, academic, wellness, etc.)
- `/contact [role]` — look up a GSA officer or campus office contact
- `/initiative` — submit a new initiative idea to GSA officers
- `/feedback` — send anonymous feedback to GSA
- `/help` — see a full list of commands
- `/qrcode` — generate a GSA-branded QR code

## Q: What is MathCafe?
**A:** MathCafe (GSA MathCafe) is a daily enrichment feature run by the GSA Gateway bot. Every morning at 9 AM Eastern time, the bot posts a math fact, puzzle, or research tidbit to the #gsa-mathcafe channel. Posts include fun facts about mathematicians, famous theorems, statistical insights, and brain teasers. Some posts include a hidden spoiler answer and a discussion thread where members can share their solutions. MathCafe is designed to spark curiosity and community among NJIT graduate students.

## Q: Where does MathCafe post and when?
**A:** MathCafe posts automatically every day at 9:00 AM Eastern Time to the #gsa-mathcafe channel in the GSA Discord server. Each post is a new math fact or puzzle from the queue. If you miss a post, you can scroll back in #gsa-mathcafe to see previous ones. Discussion threads are created for puzzle posts so members can share answers.

## Q: How do I submit a new initiative to GSA?
**A:** Use the `/initiative` command in Discord. It opens a form where you fill in the initiative title, description, your goals, timeline, and any resources needed. Your submission is saved and reviewed by GSA officers. This is the official channel for proposing new GSA programs, events, or services.

## Q: How do I send feedback to GSA?
**A:** Use the `/feedback` command. Your message is stored anonymously — your Discord ID is hashed before saving, so officers can read your feedback without knowing who sent it. It's a safe way to share concerns, suggestions, or compliments.

## Q: How do I generate a QR code with the bot?
**A:** Use `/qrcode` and provide the URL or text you want to encode. You can choose a color style: Black & White (default) or Red & White (NJIT colors). The bot returns a QR code image you can download and use for flyers, posters, or presentations.

## Q: How does the free chat in #ask-gsa work?
**A:** In the #ask-gsa channel (and in DMs with the bot), you can ask questions in plain English without using any slash command. The bot uses a Retrieval-Augmented Generation (RAG) pipeline: it searches the GSA knowledge base for relevant information, then uses a local AI model (Ollama) to generate a grounded answer. It only answers based on official GSA documents — it will not make things up.

## Q: What event reminders does the bot send?
**A:** The bot automatically sends event reminders to the appropriate channels at three intervals: 7 days before, 1 day before, and 1 hour before each event. Reminders are routed to the channel matching the event category (academic, social, wellness, etc.).

## Q: What is the daily digest?
**A:** Each morning, if there are GSA events happening within the next 7 days, the bot posts a digest to #gsa-announcements listing those upcoming events with their dates. This runs automatically so members always have a heads-up about what's coming.

## Q: How do I look up a GSA officer's contact information?
**A:** Use `/contact [role]` — for example `/contact president` or `/contact vp academic affairs`. You can also ask in #ask-gsa ("who is the GSA president?" or "how do I contact the treasurer?") and the bot will look it up for you.

## Q: What resource categories does the bot know about?
**A:** Use `/resources` to see available categories. They include: funding opportunities, academic support, wellness & mental health, career development, housing, legal & immigration, technology, and campus services. Each category lists 3–4 specific resources with links and descriptions.

## Q: Who runs and maintains GSA Gateway?
**A:** GSA Gateway is built and maintained by Mohammad Dindoost, VP of Academic Affairs at NJIT GSA. The bot runs on a dedicated machine and is designed to be always available to NJIT graduate students.
