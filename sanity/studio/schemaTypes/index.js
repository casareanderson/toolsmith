// Toolsmith knowledge base — Sanity schema (Sanity Challenge, Path One).
//
// The idea: every number a recommendation uses is a STRUCTURED value tied to the fact it
// came from, and every licence is a field, not a phrase. The validator then checks a
// recommendation by following references (GROQ joins), not by pulling digits out of prose,
// which is what made the text validator withhold "Apache-2.0" as the number 2.0.

const measurement = {
  name: 'measurement', title: 'Measurement', type: 'object',
  fields: [
    {name: 'label', type: 'string', validation: (R) => R.required(),
     description: 'What was measured, e.g. "memory in use", "storage pool used".'},
    {name: 'value', type: 'number', validation: (R) => R.required()},
    {name: 'unit', type: 'string', description: 'MB, %, GB, count, days ...'},
  ],
  preview: {select: {label: 'label', value: 'value', unit: 'unit'},
    prepare: ({label, value, unit}) => ({title: `${label}: ${value}${unit ? ' ' + unit : ''}`})},
}

const usedNumber = {
  name: 'usedNumber', title: 'Number used in the claim', type: 'object',
  fields: [
    {name: 'value', type: 'number', validation: (R) => R.required()},
    {name: 'unit', type: 'string'},
    {name: 'fact', type: 'reference', to: [{type: 'fact'}], validation: (R) => R.required(),
     description: 'The fact this number must come from. The validator checks the fact really holds it.'},
  ],
}

export const toolArea = {
  name: 'toolArea', title: 'Tool area', type: 'document',
  fields: [
    {name: 'areaId', title: 'Id', type: 'slug', validation: (R) => R.required()},
    {name: 'title', type: 'string', validation: (R) => R.required()},
    {name: 'keywords', title: 'Incumbent keywords', type: 'array', of: [{type: 'string'}],
     description: 'An area only enters the weekly rotation if a running incumbent matches one of these.'},
  ],
}

export const run = {
  name: 'run', title: 'Research run', type: 'document',
  fields: [
    {name: 'runId', type: 'string', validation: (R) => R.required(), description: 'e.g. 20260917-1030'},
    {name: 'area', type: 'reference', to: [{type: 'toolArea'}], validation: (R) => R.required()},
    {name: 'startedAt', type: 'datetime'},
    {name: 'gaps', type: 'array', of: [{type: 'string'}],
     description: 'What the gatherer could NOT look at. Not an all-clear.'},
  ],
  preview: {select: {title: 'runId', subtitle: 'area.title'}},
}

export const fact = {
  name: 'fact', title: 'Gathered fact', type: 'document',
  fields: [
    {name: 'factId', type: 'string', validation: (R) => R.required(), description: 'F001 ... within its run'},
    {name: 'run', type: 'reference', to: [{type: 'run'}], validation: (R) => R.required()},
    {name: 'kind', type: 'string', validation: (R) => R.required(),
     options: {list: ['gap', 'preference', 'rotation', 'incumbent', 'capacity', 'candidate', 'licence', 'sandbox', 'other']}},
    {name: 'statement', type: 'text', validation: (R) => R.required()},
    {name: 'measurements', type: 'array', of: [measurement],
     description: 'Every number in the statement, as a typed value. The validator only trusts these.'},
    {name: 'licenceSpdx', title: 'Licence (SPDX id)', type: 'string',
     description: 'e.g. Apache-2.0. A licence is an identifier, never a number.'},
    {name: 'source', type: 'string', description: 'How it was gathered: inventory scan, owner preference, licence file ...'},
  ],
  preview: {select: {title: 'factId', subtitle: 'statement'}},
}

export const candidateTool = {
  name: 'candidateTool', title: 'Candidate tool', type: 'document',
  fields: [
    {name: 'name', type: 'string', validation: (R) => R.required()},
    {name: 'area', type: 'reference', to: [{type: 'toolArea'}]},
    {name: 'homepage', type: 'url'},
    {name: 'licenceSpdx', title: 'Licence (SPDX id)', type: 'string'},
    {name: 'licenceClass', type: 'string', options: {list: ['OSI', 'source-available', 'non-commercial', 'unknown']}},
  ],
}

export const recommendation = {
  name: 'recommendation', title: 'Recommendation', type: 'document',
  fields: [
    {name: 'recId', type: 'string', validation: (R) => R.required(), description: 'REC 1 ... within its run'},
    {name: 'run', type: 'reference', to: [{type: 'run'}], validation: (R) => R.required()},
    {name: 'title', type: 'string', validation: (R) => R.required()},
    {name: 'verdict', type: 'string', validation: (R) => R.required(),
     options: {list: ['ADOPT-TRIAL', 'KEEP', 'REJECT', 'WATCH']}},
    {name: 'candidate', type: 'reference', to: [{type: 'candidateTool'}]},
    {name: 'claim', type: 'text', validation: (R) => R.required(), description: "The model's wording."},
    {name: 'citedFacts', type: 'array', of: [{type: 'reference', to: [{type: 'fact'}]}]},
    {name: 'numbersUsed', type: 'array', of: [usedNumber],
     description: 'Each number the claim relies on, tied to the fact that must hold it.'},
    {name: 'textValidator', title: 'What the text validator said (2026-09)', type: 'object',
     fields: [
       {name: 'status', type: 'string', options: {list: ['published', 'withheld']}},
       {name: 'reasons', type: 'array', of: [{type: 'string'}]},
     ]},
  ],
  preview: {select: {title: 'title', subtitle: 'verdict'}},
}

// Report prose: the source the Context Knowledge Base indexes. Recall, never evidence --
// a number in a report backs nothing; only a shown recommendation's structured values do.
const report = {
  name: 'report', title: 'Run report (prose)', type: 'document',
  fields: [
    {name: 'title', type: 'string', validation: (R) => R.required()},
    {name: 'kind', type: 'string', validation: (R) => R.required(),
     options: {list: ['report', 'withheld']},
     description: 'report = the weekly write-up; withheld = the validator\'s reasons file.'},
    {name: 'run', type: 'reference', to: [{type: 'run'}], validation: (R) => R.required()},
    {name: 'body', type: 'text', rows: 30, validation: (R) => R.required(), description: 'Markdown, as written.'},
    {name: 'sourceFile', type: 'string', description: 'Path in the public toolsmith repo.'},
  ],
  preview: {select: {title: 'title', subtitle: 'kind'}},
}

export const schemaTypes = [toolArea, run, fact, candidateTool, recommendation, report]
