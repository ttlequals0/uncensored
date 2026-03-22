# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Core Principles

- NEVER create mock data or simplified components unless explicitly told to do so
- NEVER replace existing complex components with simplified versions - always fix the actual problem
- ALWAYS work with the existing codebase - do not create new simplified alternatives
- ALWAYS find and fix the root cause of issues instead of creating workarounds
- NEVER remove failed tests, ALWAYS fix them
- NEVER skip tests - always wait for them to finish and fix any failures
- NEVER just agree - always state your reasons for choices made. We are a TEAM

## Change Management

- ALWAYS track all changes in CHANGELOG.md
- ALWAYS refer to CHANGELOG.md when working on tasks
- Build new changes into a feature / fix branch off of main
- NEVER commit directly to main or master branches

## Testing Requirements

- Run ALL tests after code changes
- NEVER skip failed tests, ALWAYS fix the problem
- Python testing should use venv
- Always run test suite before each build and make sure the app will actually start
- Always make sure the App builds successfully

## Security & Quality

- Always scrub out all sensitive data in the repo
- This is a public repo -- never include environment-specific info (domains, server URLs, API tokens, internal hostnames) in PRs, commit messages, or descriptions
- NO emojis in any code or documentation
- Don't add yourself or Claude to git commits
- Imports should be at top of file not inline

## Workspace

- Only work in /Users/dkrachtus/repos/uncensored
