---
tags: [moc, architecture]
---
# Architecture MOC

Maps of the system's components and how they connect.

## Overview
- [[System Overview]] — end-to-end pipeline diagram and component map

## Core Components
- [[Schwab Auth]] — dual OAuth2 sessions (market + account)
- [[Signal Scanner]] — two-stage scan pipeline (Stage A fast filter + Stage B enrichment)
- [[Execution Engine]] — order placement, guardrail wrapper, plugin hooks
- [[Guardrails]] — risk limits, circuit breaker, sector caps

## Intelligence Layer
- [[Advisory Model]] — calibrated P(up in 10 days) probability scoring
- [[Stage 2 Analysis]] — Weinstein Stage 2 trend qualification
- [[VCP Detection]] — Volume Contraction Pattern identification
- [[Sector Strength]] — relative performance vs SPY

## Interfaces
- [[WebApp Dashboard]] — FastAPI local dashboard (scan, approve, portfolio)
- [[SaaS API]] — multi-tenant production API (Supabase, Stripe, Celery)
- [[Discord Integration]] — webhooks, slash commands, notification types

## Data
- [[Database Schema]] — all SQLAlchemy tables and relationships
