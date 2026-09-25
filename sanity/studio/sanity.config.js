import {defineConfig} from 'sanity'
import {structureTool} from 'sanity/structure'
import {visionTool} from '@sanity/vision'
import {schemaTypes} from './schemaTypes'

export default defineConfig({
  name: 'toolsmith',
  title: 'Toolsmith knowledge base',
  projectId: 'en9phc0q',
  dataset: 'toolsmith',
  plugins: [structureTool(), visionTool()],
  schema: {types: schemaTypes},
})
