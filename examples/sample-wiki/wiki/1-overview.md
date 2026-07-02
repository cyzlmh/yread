# Overview

`yread` turns a local source repository into an architecture-first Markdown wiki. It first builds a project profile and catalog, then starts independent page agents to inspect evidence files and write focused documentation for human understanding.

```mermaid
flowchart LR
    Repo[Local repository] --> Profile[Project Profile]
    Profile --> Catalog[Catalog Agent]
    Catalog --> WikiJson[wiki.json]
    WikiJson --> PageAgents[Page Agents]
    PageAgents --> Pages[Markdown pages]
```

This static sample demonstrates the output layout. It is not a real model-generated run.
