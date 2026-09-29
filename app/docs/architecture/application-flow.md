# Application Flow

Detailed flowcharts showing how AutoApply processes each job from discovery to submission.

## High-Level Pipeline

```mermaid
flowchart LR
    A[Start Bot] --> B[Search]
    B --> C[Score & Filter]
    C --> D[Generate Docs]
    D --> E{Review Mode?}
    E -->|No| F[Apply]
    E -->|Yes| G[Wait for User]
    G -->|Approve| F
    G -->|Skip| H[Next Job]
    G -->|Edit| F
    G -->|Manual| I[Save as Manual]
    F --> J[Save Result]
    J --> K{More Jobs?}
    K -->|Yes| C
    K -->|No| L[Wait Interval]
    L --> B
```

---

## 1. Bot Startup

```mermaid
flowchart TD
    A[POST /api/bot/start] --> B{Bot thread alive?}
    B -->|Yes| C[Return 409 Conflict]
    B -->|No| D[Load config]
    D --> E{Config exists?}
    E -->|No| F[Return 400 Error]
    E -->|Yes| G[bot_state.start]
    G --> H[Set status = running]
    H --> I[Set stop_flag = False]
    I --> J[Record start time]
    J --> K[Spawn bot-worker thread]
    K --> L[Return 200 OK]
    K --> M[Thread: Initialize BrowserManager]
    M --> N[Thread: Enter main loop]

    style A fill:#4a9eff,color:white
    style C fill:#ff6b6b,color:white
    style F fill:#ff6b6b,color:white
    style L fill:#51cf66,color:white
```

### Scheduler Auto-Start

```mermaid
flowchart TD
    A[Scheduler Thread] --> B{Every 60 seconds}
    B --> C{Schedule enabled?}
    C -->|No| B
    C -->|Yes| D{Current day in schedule?}
    D -->|No| G
    D -->|Yes| E{Current time in window?}
    E -->|Yes| F{Bot already running?}
    F -->|Yes| B
    F -->|No| H[Auto-start bot]
    H --> B
    E -->|No| G{Bot auto-started?}
    G -->|Yes| I[Auto-stop bot]
    G -->|No| B
    I --> B
```

---

## 2. Main Bot Loop

```mermaid
flowchart TD
    A[Main Loop Start] --> B{Stop flag set?}
    B -->|Yes| Z[Close browser, exit thread]
    B -->|No| C{Status = paused?}
    C -->|Yes| D[Sleep 1s]
    D --> B
    C -->|No| E[For each enabled platform]
    E --> F[searcher.search]
    F --> G[For each raw job]
    G --> H{Stop flag?}
    H -->|Yes| Z
    H -->|No| I{Paused?}
    I -->|Yes| J[Wait until resumed]
    J --> H
    I -->|No| K[Increment jobs_found]
    K --> L[Emit FOUND event]
    L --> M{Daily limit reached?}
    M -->|Yes| N[Break to next cycle]
    M -->|No| O[Score & Filter]
    O --> P{Passed filter?}
    P -->|No| Q[Emit FILTERED event]
    Q --> G
    P -->|Yes| R0[Save Job Description as HTML]
    R0 --> R[Generate Documents]
    R --> S{Review mode?}
    S -->|No| T[Apply to Job]
    S -->|Yes| U[Review Gate]
    U --> V{Decision?}
    V -->|Approve| T
    V -->|Edit| W[Update cover letter]
    W --> T
    V -->|Skip| X[Emit SKIPPED]
    X --> G
    V -->|Manual| Y[Save as manual_required]
    Y --> G
    V -->|Stop| Z
    T --> AA[Save to Database]
    AA --> AB[Emit result event]
    AB --> AC[Sleep delay]
    AC --> G
    N --> AD[Sleep search interval]
    AD --> A

    style Z fill:#ff6b6b,color:white
    style L fill:#4a9eff,color:white
    style Q fill:#868e96,color:white
    style AB fill:#51cf66,color:white
```

---

## 3. Job Scoring & Filtering

```mermaid
flowchart TD
    A[score_job] --> B{Already applied?}
    B -->|Yes| C[FAIL: Already applied]
    B -->|No| D{Company blacklisted?}
    D -->|Yes| E[FAIL: Blacklisted]
    D -->|No| F{Excluded keyword in title/desc?}
    F -->|Yes| G[FAIL: Excluded keyword]
    F -->|No| H[Calculate Score]

    H --> I[Title Match: 0-35 pts]
    H --> J[Salary Match: 0-20 pts]
    H --> K[Location Match: 0-20 pts]
    H --> L[Keyword Match: 0-25 pts]

    I --> M[Sum score]
    J --> M
    K --> M
    L --> M

    M --> N{Score >= min threshold?}
    N -->|Yes| O[PASS: score, pass_filter=True]
    N -->|No| P[FAIL: Below threshold]

    style C fill:#ff6b6b,color:white
    style E fill:#ff6b6b,color:white
    style G fill:#ff6b6b,color:white
    style P fill:#ff6b6b,color:white
    style O fill:#51cf66,color:white
```

### Scoring Breakdown

```mermaid
flowchart LR
    subgraph Title [Title Match: 0-35]
        T1[Exact match] -->|+35| TS
        T2[≥50% word overlap] -->|+20| TS
        T3[No match] -->|+0| TS[Score]
    end

    subgraph Salary [Salary Match: 0-20]
        S1[Meets min salary] -->|+20| SS
        S2[No min set] -->|+20| SS
        S3[Salary unknown] -->|+10| SS
        S4[Below min] -->|+0| SS[Score]
    end

    subgraph Location [Location Match: 0-20]
        L1[Exact location] -->|+20| LS
        L2[Remote match] -->|+20| LS
        L3[Same country] -->|+10| LS
        L4[No match] -->|+0| LS[Score]
    end

    subgraph Keywords [Keyword Match: 0-25]
        K1[+5 per matching keyword] --> KS[Score]
        K2[Max 25 pts] --> KS
    end
```

---

## 4. Document Generation

```mermaid
flowchart TD
    A[Generate Documents] --> B[Emit GENERATING event]
    B --> C[Read experience .txt files]
    C --> D{AI provider configured?}
    D -->|No| E[Use fallback resume PDF]
    E --> F[Use static cover letter template]
    F --> G[Return fallback paths]

    D -->|Yes| H[Build resume prompt]
    H --> I[LLM Call 1: Generate Resume]
    I --> J{API call succeeded?}
    J -->|No| K[RuntimeError]
    K --> E
    J -->|Yes| L[Save resume as .md]
    L --> M[Render resume to PDF via ReportLab]

    M --> N[Build cover letter prompt]
    N --> O[LLM Call 2: Generate Cover Letter]
    O --> P{API call succeeded?}
    P -->|No| K
    P -->|Yes| Q[Save cover letter as .txt]
    Q --> R[Return paths: resume.pdf, cover_letter.txt]

    style G fill:#ffd43b,color:black
    style R fill:#51cf66,color:white
    style K fill:#ff6b6b,color:white
```

### LLM API Call Routing

```mermaid
flowchart TD
    A[invoke_llm] --> B{Provider?}
    B -->|Anthropic| C[POST api.anthropic.com/v1/messages]
    B -->|OpenAI| D[POST api.openai.com/v1/chat/completions]
    B -->|Google| E[POST generativelanguage.googleapis.com]
    B -->|DeepSeek| F[POST api.deepseek.com/v1/chat/completions]

    C --> G[Parse: content.0.text]
    D --> H[Parse: choices.0.message.content]
    E --> I[Parse: candidates.0.content.parts.0.text]
    F --> H

    G --> J[Return trimmed text]
    H --> J
    I --> J

    C --> K{Status != 200?}
    D --> K
    E --> K
    F --> K
    K -->|Yes| L[Raise RuntimeError]

    style L fill:#ff6b6b,color:white
    style J fill:#51cf66,color:white
```

---

## 5. Review Gate

```mermaid
flowchart TD
    A{Apply mode?} -->|full_auto| B[Skip review, go to Apply]
    A -->|review / watch| C[Emit REVIEW event to dashboard]
    C --> D[Set awaiting_review = True]
    D --> E[BLOCK: Wait for user decision]

    E --> F{Decision received?}
    F -->|approve| G[Proceed to Apply]
    F -->|edit| H[Replace cover letter text]
    H --> G
    F -->|skip| I[Emit SKIPPED event]
    I --> J[Next job]
    F -->|manual| K[Save as manual_required]
    K --> L[Emit APPLIED: manual]
    L --> J
    F -->|stop flag| M[Exit bot loop]

    style B fill:#4a9eff,color:white
    style G fill:#51cf66,color:white
    style I fill:#868e96,color:white
    style K fill:#ffd43b,color:black
    style M fill:#ff6b6b,color:white
```

---

## 6. Apply to Job

```mermaid
flowchart TD
    A[Apply to Job] --> B[Emit APPLYING event]
    B --> C[Detect ATS platform from URL]
    C --> D{Platform supported?}
    D -->|No| E[Return: manual_required]

    D -->|Yes| F{Which platform?}
    F -->|LinkedIn| G[LinkedInApplier]
    F -->|Indeed| H[IndeedApplier]
    F -->|Greenhouse| I[GreenhouseApplier]
    F -->|Lever| J[LeverApplier]
    F -->|Workday| K[WorkdayApplier]
    F -->|Ashby| L[AshbyApplier]

    G --> M[Fill form with human-like delays]
    H --> M
    I --> M
    J --> M
    K --> M
    L --> M

    M --> N{CAPTCHA detected?}
    N -->|Yes| O[Return: captcha_detected]
    N -->|No| P[Upload resume PDF]
    P --> Q[Paste cover letter]
    Q --> R[Submit application]
    R --> S{Submission successful?}
    S -->|Yes| T[Return: success=True]
    S -->|No| U[Return: error with message]

    style E fill:#ffd43b,color:black
    style O fill:#ff6b6b,color:white
    style T fill:#51cf66,color:white
    style U fill:#ff6b6b,color:white
```

### ATS Detection

```mermaid
flowchart LR
    A[Job URL] --> B{URL contains?}
    B -->|greenhouse.io| C[Greenhouse]
    B -->|lever.co| D[Lever]
    B -->|myworkdayjobs.com| E[Workday]
    B -->|ashbyhq.com| F[Ashby]
    B -->|linkedin.com| G[LinkedIn]
    B -->|indeed.com| H[Indeed]
    B -->|taleo.net| I[Taleo - unsupported]
    B -->|icims.com| J[iCIMS - unsupported]
    B -->|None match| K[Use source platform]

    style I fill:#ffd43b,color:black
    style J fill:#ffd43b,color:black
```

---

## 7. Save & Emit Result

```mermaid
flowchart TD
    A[ApplyResult] --> B{result.success?}
    B -->|Yes| C[status = applied]
    C --> D[Increment applied counter]
    D --> E[Emit APPLIED event]

    B -->|No| F{result.captcha?}
    F -->|Yes| G[status = error]
    G --> H[Increment error counter]
    H --> I[Emit CAPTCHA event]

    F -->|No| J{result.manual?}
    J -->|Yes| K[status = manual_required]
    K --> L[Emit APPLIED: manual]

    J -->|No| M[status = error]
    M --> N[Increment error counter]
    N --> O[Emit ERROR event]

    E --> P[Save to applications table]
    I --> P
    L --> P
    O --> P

    P --> Q[Sleep delay_between_applications]
    Q --> R[Next job]

    style C fill:#51cf66,color:white
    style G fill:#ff6b6b,color:white
    style K fill:#ffd43b,color:black
    style M fill:#ff6b6b,color:white
```

---

## 8. Bot State Machine

```mermaid
stateDiagram-v2
    [*] --> Stopped
    Stopped --> Running: start()
    Running --> Paused: pause()
    Running --> Stopped: stop()
    Paused --> Running: resume()
    Paused --> Stopped: stop()
    Running --> AwaitingReview: begin_review()
    AwaitingReview --> Running: set_review_decision()
    AwaitingReview --> Stopped: stop()
```

### State Fields

```
BotState
├── status: "stopped" | "paused" | "running"
├── stop_flag: bool
├── jobs_found_today: int
├── applied_today: int
├── errors_today: int
├── start_time: datetime
├── awaiting_review: bool
├── review_decision: str | None
└── review_edits: str | None
```

---

## 9. SocketIO Event Flow

```mermaid
sequenceDiagram
    participant Bot as Bot Thread
    participant Server as Flask Server
    participant Client as Dashboard UI

    Bot->>Server: emit("FOUND", job_title, company)
    Server->>Client: feed_event {type: "FOUND"}
    Server->>Client: bot_status {jobs_found: N}

    Bot->>Server: emit("FILTERED", reason)
    Server->>Client: feed_event {type: "FILTERED"}

    Bot->>Server: emit("GENERATING", job_title)
    Server->>Client: feed_event {type: "GENERATING"}

    alt Review Mode
        Bot->>Server: emit("REVIEW", job_title, cover_letter)
        Server->>Client: feed_event {type: "REVIEW", cover_letter}
        Client->>Server: POST /api/bot/review/approve
        Server->>Bot: set_review_decision("approve")
    end

    Bot->>Server: emit("APPLYING", job_title)
    Server->>Client: feed_event {type: "APPLYING"}

    Bot->>Server: emit("APPLIED", job_title)
    Server->>Client: feed_event {type: "APPLIED"}
    Server->>Client: bot_status {applied: N}
```

---

## 10. Error Handling

```mermaid
flowchart TD
    A[Error during apply] --> B{Error type?}

    B -->|CAPTCHA| C[Log CAPTCHA event]
    C --> D[Skip job, continue]

    B -->|Network timeout| E[Log ERROR event]
    E --> D

    B -->|Form changed| F[Log ERROR event]
    F --> D

    B -->|Browser crash| G[Log ERROR event]
    G --> H[Close browser]
    H --> I[Exit bot loop]

    B -->|LLM API error| J[Use fallback templates]
    J --> K[Continue with static docs]

    B -->|Unhandled exception| L[Log crash]
    L --> M[Emit ERROR: Bot crashed]
    M --> H

    style C fill:#ff6b6b,color:white
    style E fill:#ff6b6b,color:white
    style F fill:#ff6b6b,color:white
    style J fill:#ffd43b,color:black
    style L fill:#ff6b6b,color:white
```
