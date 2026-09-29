# How AI Generation Works

AutoApply uses your configured AI provider to create a unique resume and cover letter for every job application. Here's what happens behind the scenes.

## The Process

```
Your experience files  ─┐
                        ├──▶  LLM API  ──▶  Tailored Resume (PDF)
Job description        ─┘               ──▶  Cover Letter (text)
```

1. **Read your experience** — AutoApply reads all `.txt` files from your experience folder
2. **Build a prompt** — Your experience + the job description + your profile info are combined into a detailed prompt
3. **Generate resume** — The AI creates a resume in Markdown, selecting only the most relevant experiences for this specific job
4. **Generate cover letter** — A second API call produces a professional cover letter that references specific details from the job posting
5. **Render PDF** — The Markdown resume is converted to an ATS-safe PDF

## Supported AI Providers

Configure your provider in **Settings → AI Provider**.

| Provider | Default Model | API Endpoint |
|----------|--------------|-------------|
| **Anthropic** | `claude-sonnet-4-20250514` | Anthropic Messages API |
| **OpenAI** | `gpt-4o` | OpenAI Chat Completions |
| **Google** | `gemini-2.0-flash` | Google Gemini API |
| **DeepSeek** | `deepseek-chat` | DeepSeek Chat API |

You can override the default model in the model field. Leave it blank to use the default.

## What "ATS-Safe" Means

ATS (Applicant Tracking Systems) are software that companies use to screen resumes. Many ATS systems can't read fancy formatting. AutoApply generates PDFs that are:

- **Helvetica font only** — universally supported
- **Single-column layout** — no side panels or columns
- **No colors, tables, or images** — just clean text
- **Proper headings** — Summary, Experience, Skills, Education

This means your resume will be correctly parsed by LinkedIn, Indeed, Greenhouse, Lever, Workday, and other common ATS platforms.

## What the AI Is Told

The resume prompt instructs the AI to:

- Select only experiences relevant to **this specific job**
- Quantify achievements with numbers where your files include them
- Use strong action verbs
- Keep content to one page
- **Never invent or exaggerate** anything not in your experience files

The cover letter prompt instructs the AI to:

- Write 3 professional paragraphs
- Reference specific details from the job description
- Match the tone to the company culture
- Not repeat the resume content
- Avoid filler phrases like "I am excited to apply"

## Where Generated Documents Are Saved

```
~/.autoapply/profile/
├── resumes/              # PDF and Markdown files
│   ├── abc123_acme-corp_2026-03-09.pdf
│   └── abc123_acme-corp_2026-03-09.md
├── cover_letters/        # Plain text files
│   └── abc123_acme-corp_2026-03-09.txt
└── job_descriptions/     # Original job postings (HTML)
    └── abc123_acme-corp_2026-03-09.html
```

Each file is named with the job ID, company name, and date.

## Without an AI Provider

If no API key is configured, AutoApply falls back to:

- **Resume**: Your uploaded fallback PDF (set in Settings > Profile > Fallback Resume)
- **Cover letter**: A static template (set in Settings > Bot > Cover Letter Template)

These aren't tailored to each job, but applications still go out. A yellow banner on the dashboard reminds you to add an API key for better results.

## Privacy

Your experience files and job descriptions are sent to your configured AI provider's API (Anthropic, OpenAI, Google, or DeepSeek) for document generation. No data is sent if no API key is configured. AutoApply does not store or transmit your data to any other service.
