import { useMutation } from '@tanstack/react-query'
import { apiUpload, type RoleHeaders } from './client'
import type { UploadBatchResponse } from './types'

export interface UploadBatchInput {
  audioFiles: File[]
  metadataCsv: File
}

export function useUploadBatch(headers: RoleHeaders | null) {
  return useMutation({
    mutationFn: async ({ audioFiles, metadataCsv }: UploadBatchInput) => {
      if (!headers) {
        throw new Error('No active identity selected.')
      }
      const formData = new FormData()
      for (const file of audioFiles) {
        formData.append('audio_files', file, file.name)
      }
      formData.append('metadata_csv', metadataCsv, metadataCsv.name)
      return apiUpload<UploadBatchResponse>('/api/uploads/batch', formData, headers)
    },
  })
}
