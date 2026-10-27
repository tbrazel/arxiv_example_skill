---
name: researching-arxiv-papers
description: Finds, retrieves, reads, and cites arXiv preprints. Use when the user mentions arXiv, gives an arXiv ID or URL, or asks to search, summarize, compare, or cite preprints or recent research papers.
---

# Research reading

## Clarify the goal

Before diving in, work out what the user actually needs:

- A quick sense of what a paper is about
- A detailed understanding of one technique
- A comparison between several approaches
- Material for their own writing

Adjust depth accordingly. A user skimming a field needs breadth; a user checking a proof needs depth.

## Read in passes

1. First pass: title, abstract, section headings, and the conclusion. Decide whether the paper is relevant.
2. Second pass: read the main body, paying attention to figures, tables, and the statement of results. Skip proofs.
3. Third pass, only if needed: work through the technical details and proofs, checking each step.

Most questions can be answered after the second pass.

## Find the core idea

- Ask what problem the paper sets out to solve, and why earlier approaches fell short.
- Locate the one or two moves that make the method work. Most papers have a single key insight surrounded by engineering.
- Separate the idea from its implementation: would the insight survive a different architecture, dataset, or proof technique?
- Note what the paper takes as given. Assumptions in the setup often matter more than the headline result.

## Figures and tables

- Read the axes, units, and captions before the curves.
- Check what the baseline is and whether it is a fair comparison.
- Look at the spread, not just the best number: error bars, number of seeds, variance across settings.
- Tables often carry the real story. Compare rows the authors don't highlight.

## Mathematical content

- Restate each main theorem in plain words before reading its proof.
- Track which hypotheses each result uses, and check whether they are used in the proof.
- Work a small example by hand when a definition is abstract.
- When notation is dense, keep a short glossary of symbols as you go.

## Experimental content

- Identify the independent and dependent variables, and what was held fixed.
- Look for ablations that isolate the contribution of each component.
- Consider whether the evaluation measures what the paper claims to improve.
- Note the compute, data, and tuning budget; results may depend on them.

## Take structured notes

For each paper, capture:

- the problem being addressed
- the main idea in one sentence
- the key results
- the assumptions the results depend on
- open questions or limitations the authors mention
- how it relates to other work the user cares about

## Summarize clearly

- Lead with the main contribution.
- Use the paper's own terminology, defined briefly where needed.
- Separate what the paper shows from what it speculates about.
- Keep summaries short unless the user asks for more.
- Use plain language before notation; introduce notation only when it saves words.

## Compare papers

When comparing several papers:

- Pick shared dimensions: problem setting, method, assumptions, results, limitations.
- Note where the papers genuinely disagree versus where they study different settings.
- Avoid ranking papers unless the user asks; describe trade-offs instead.
- A small table often helps when there are more than two papers.

## Keep track of several papers

- Give each paper a short handle (first author and topic) and use it consistently.
- Group papers by approach rather than by date when summarizing a field.
- Note which papers build directly on which, so the user can see lines of work.

## Explain to different audiences

- For specialists: be concise and precise; skip background they know.
- For adjacent fields: give one or two sentences of context before the result.
- For general audiences: use an analogy or a worked example, then state the result.

## Writing style

- Prefer short paragraphs.
- Use consistent terminology throughout an answer.
- Put the answer to the user's question first, then supporting detail.
- When listing several items, keep each to one or two sentences.

## Follow-up

Offer a natural next step when it would help: going deeper into one section,
comparing with a related approach, or turning notes into a short write-up.
