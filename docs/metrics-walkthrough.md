# How the OCR scoreboard works, and why it gives the wrong answer

## The situation, in one paragraph

This repository holds the text of New York City executive orders. Most of those
orders exist only as scanned paper. A scan is a picture, not text, so a machine
has to read the picture and type out what it sees. Two machines did that job.
The first was Tesseract, a traditional character-reader from the 2000s. The
second was a vision language model, which I will call "the model". The model's
output is plainly better when you read it. The report in `post1974_ocr_report.md`
says the opposite. This document explains every measurement in that report, why
each one exists, and where each one goes wrong.

## What the two outputs actually look like

Here is the top of a real order, 1980-EO-042, as Tesseract typed it:

    we peer 1
    ESTE
    TE
    RECO
    <:
    cy the
    Executive or
    Wo. re
    ten
    a
    February I, 1980
    |
    Orde
    ,
    EQUAL EMPLOYMENT OPPORTUNITY

And here is the same part of the same page as the model typed it:

    OFFICE OF THE MAYOR
    EXECUTIVE ORDER NO. 42
    February 1, 1980
    EQUAL EMPLOYMENT OPPORTUNITY

The last line of the Tesseract version reads:

    g: 3. Eftective Date. Api prder sha} take effect 'snondiatohy
    od
    aed
    EDWARD ZL. KOCH, . 'waroll' "

The model's version of that same line reads:

    Section 5. Effective Date. This order shall take effect immediately.
    EDWARD I. KOCH, MAYOR.

No reasonable person would call the first version acceptable. Keep that in mind
as we go through the measurements, because the measurements do call it
acceptable.

## Why any of this is measured at all

There are about 1,900 scanned pages here. Nobody is going to read all of them
by hand. So the pipeline needs a cheap, automatic way to sort the output into
two piles: "this is fine, publish it" and "a person needs to look at this one".
Every measurement below exists to serve that one purpose. That is a completely
reasonable goal. The problem is not the goal. The problem is that the specific
tests chosen cannot tell the two versions above apart.

## Measurement 1: how long is it?

The simplest check. Count the characters before, count the characters after,
and divide.

For 1980-EO-042: Tesseract produced 1,425 characters, the model produced 1,217.
The ratio is 0.85.

**Why it exists.** If the new reader returns almost nothing, something broke.
A page that used to yield 3,000 characters and now yields 40 is a failure you
want to catch immediately, without reading anything.

**Where it goes wrong.** Nowhere serious. It is doing its job. But notice that
it flags the model as having *lost* 15 percent of the text, when what the model
actually dropped was `we peer 1`, `ESTE`, `TE`, `RECO`, `<:` and the rest of the
garbage. Shorter is better here, and the number cannot know that. In the whole
run only one document out of 1,019 tripped this check.

## Measurement 2: does it look like English?

This is the one that matters most, and the one that fails hardest.

The test takes every run of letters that is two or more characters long, and
asks of each one: could this be an English word? It then reports the share that
pass. A score of 1.00 means every word looked plausible. The pipeline treats
0.90 and above as good, and below 0.70 as bad.

Here is exactly how it decides whether one word is plausible:

1. Is it made only of letters? If not, reject.
2. Is it 20 letters or fewer? If not, reject.
3. Does it contain at least one of a, e, i, o, u? If not, reject.
4. Does it contain five consonants in a row? If so, reject.
5. Otherwise, accept.

**There is no dictionary.** That is the whole test.

**Why it exists.** It is fast, it needs no word list, and it catches the classic
failure of an old character-reader: a page it cannot read at all comes back as
`nnnnn xzcvbn qqrtp`. Those strings have no vowels or long consonant runs, so
they fail, and the score collapses. As a smoke alarm for total garbage, it works.

**Where it goes wrong.** Look at what passes rules 1 through 5:

| Word from the Tesseract output | Verdict | Why |
|---|---|---|
| `Eftective` | passes | has vowels, no long consonant run |
| `snondiatohy` | passes | has vowels, no long consonant run |
| `waroll` | passes | has vowels, no long consonant run |
| `prder` | passes | has a vowel |
| `vaya` | passes | has vowels |
| `ESTE` | passes | has vowels |
| `RECO` | passes | has vowels |

And look at what fails:

| Word from the *model's* output | Verdict | Why |
|---|---|---|
| `By` | **fails** | `y` does not count as a vowel |
| `by` | **fails** | same |

So the test rewards pronounceable nonsense and punishes the correct word "By".

The result on 1980-EO-042: Tesseract scores 0.954, the model scores 0.989. Both
are above the 0.90 "good" line. The page that begins `we peer 1 / ESTE / TE /
RECO` is officially good English.

This is not a one-off. Across all 1,019 documents, Tesseract's median score is
0.988, and 1,012 of the 1,019 score 0.95 or higher. The pass mark is 0.90.
**Tesseract was already at the top of the scale before the model ever ran.**
There was nothing left to win. A test where everyone already scores 99 percent
cannot tell you who is better.

## Measurement 3: how many strange characters?

Count every character that is not a letter, a digit, a space, or ordinary
punctuation. Divide by the total number of characters. Anything at or below
0.05, meaning 5 percent, counts as good.

**Why it exists.** A scan with a coffee stain or a fold produces bursts of
`|||`, `~~~`, `{}` and `¢`. Those are a real signal that the reader was
guessing. Counting them is cheap and sensible.

**Where it goes wrong.** Three separate ways.

First, the same saturation problem. Tesseract's median score is 0.0002, against
a limit of 0.05. It is already 250 times better than it needs to be. Its
garbage is mostly ordinary lowercase letters, which this test does not count.

Second, the division works against the better reader. The score is strange
characters divided by *total* characters. Tesseract pads the bottom of that
fraction with hundreds of characters of junk prose. The model strips them out.
So one identical stray symbol produces a bigger score for the shorter, cleaner
document. Being concise is penalised.

Third, and worst: the model writes section headings in bold, using the standard
markdown convention of two asterisks. Like this:

    Section 1: **Office Established.** The New York City Energy and
    Telecommunications Office (the "Office") is hereby established...

The asterisk is not on the approved character list. So every bold heading counts
as damage. Across the run, asterisks are the single most common "strange
character" in the model's output, 3,087 of them, and 97 documents contain them.
In document 1986-EO-095 this alone moves the score from 0.000 to 0.014. The
model is being marked down for formatting the document correctly.

## Measurement 4: the grade

Measurements 2 and 3 are combined into a single grade with three values:
`clean`, `minor-noise`, and `needs-review`. Roughly: a document is `clean` if it
looks like English, has few strange characters, and the reader found a
recognisable header line such as "OFFICE OF THE MAYOR".

**Why it exists.** This is the actual output of the whole exercise. It is the
label that decides whether a human ever looks at the document.

**Where it goes wrong.** It inherits every flaw of measurements 2 and 3. If both
inputs are saturated, so is the grade. And there is one more problem, which is
the biggest of all, and which has nothing to do with the text.

## Measurement 5: the ink check, which only one side takes

This is the decisive one, so it is worth being precise.

When the model reads a page, it also returns the position of every block of text
it found, as a rectangle. The pipeline then does something clever: it looks at
the original picture, finds every dark pixel, and asks what share of that dark
ink falls inside one of the rectangles the model returned. If the model silently
skipped a paragraph, the words are still sitting there on the page as ink,
outside every rectangle. The share drops. The pipeline requires 98 percent, and
raises a warning below that.

**Why it exists.** It is the only check in the whole system that can catch
*missing* content. A model that quietly drops a paragraph returns text that
reads perfectly. Nothing in the words themselves gives it away. Only the ink
does. This is a genuinely good idea and it should be kept.

**Where it goes wrong.** Two things.

First, 98 percent is very tight for a stamped, signed, hole-punched, hand-annotated
1970s carbon copy. I looked at what the uncovered ink actually is on the pages
that just miss the line. It is date stamps in the right margin, file numbers,
page numbers, handwritten notes, and signatures. The model correctly leaves
those out of the order's text. The check then treats that correct decision as
evidence of failure. Of the 229 pages that fail, 91 fail by less than half a
percentage point.

Second, and this is the heart of the matter: **Tesseract never takes this test.**
Tesseract does not return rectangles, so there is no ink measurement for it, so
it can never fail. The report then puts the two side by side as though they had
sat the same exam.

The effect is not small. Any page failing the ink check forces the *entire*
document to `needs-review`, no matter how good the text is. I checked all 140
documents that the report shows dropping from `clean` to `needs-review`.
**139 of them were downgraded by the ink check alone. Exactly one was downgraded
because of anything to do with its text.**

## Measurement 6: the head-to-head count

The report says "481 of 1,019 improved or held on BOTH word-ratio and
junk-ratio". This counts a document as a win for the model only if it scores at
least as well on measurement 2 *and* at least as well on measurement 3.

**Why it exists.** A reasonable instinct. You do not want to accept a change
that improves one thing by wrecking another.

**Where it goes wrong.** It demands a tie or better on two measurements that are
both pinned near their ceiling, where the only movement left is noise. 407
documents "lose" on the English test. Every single one of those losses is
smaller than 0.01, and the typical loss is 0.0001. That is a fraction of one
word in a document of several hundred. 328 "lose" on the strange-character test,
by a typical margin of 0.003, against a limit of 0.05.

So roughly half the corpus is recorded as "not an improvement" on the strength
of differences too small to see. Requiring both to hold at once roughly squares
the problem.

## What the numbers say once the unfair test is removed

I re-ran the grade on the identical text, with one change: I stopped letting the
ink check override the grade, and judged the text on the text alone.

| | clean | minor-noise | needs-review |
|---|---|---|---|
| Tesseract | 923 | 48 | 48 |
| Model, as the report presents it | 802 | 7 | 210 |
| Model, judged on its text | **976** | 13 | 30 |

The report shows the model 121 documents behind. On the same text, judged the
same way as Tesseract, it is 53 documents ahead. The 174-document swing is
entirely the ink check.

## What to change

1. **Stop letting the ink check set the grade.** Keep the check. It is the only
   thing here that can find missing paragraphs, and that is worth a great deal.
   But report it as its own separate list of pages to eyeball. "Is this text
   readable?" and "did we lose a paragraph?" are two different questions and
   they need two different answers. Combining them means the answer to the
   second one destroys the answer to the first.

2. **Loosen the ink threshold, or stop counting the margins.** Ignore ink in the
   outer margin and in the signature block, or drop the bar from 98 percent to
   something a stamped carbon copy can actually clear. Right now the check fires
   mostly on date stamps.

3. **Give the English test a dictionary, or retire it.** A test that accepts
   `snondiatohy` and rejects `by` is not measuring English. If a word list is
   too slow, at minimum add `y` to the vowels and stop treating this score as
   evidence of quality. Use it only as the smoke alarm it was designed to be.

4. **Ignore differences too small to matter.** Replace "improved or tied" with
   "improved or got no worse than 0.01". A metric that reports noise as
   regression trains you to ignore it.

5. **Add asterisks and square brackets to the allowed characters**, or strip the
   markdown formatting before measuring. Do not mark the model down for bolding
   a heading.

6. **Compare like with like.** Any future scoreboard should apply the same tests
   to both sides. If one side cannot take a test, that test does not belong in
   the comparison table. It belongs in its own section, clearly labelled as
   applying to one side only.
