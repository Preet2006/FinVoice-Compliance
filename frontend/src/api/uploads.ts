import { useMutation } from '@tanstack/react-query'
import { apiUpload, type RoleHeaders } from './client'
import type { UploadBatchResponse } from './types'

export interface UploadBatchInput {
  audioFiles: File[]
  metadataCsv: File
}

export interface UploadSingleInput {
  audioFile: File
  callId?: string
  callerId: string
  officeId: string
  accountType: 'personal_loan' | 'credit_card'
  startTime: string
  language: string
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

export function useUploadSingle(headers: RoleHeaders | null) {
  return useMutation({
    mutationFn: async ({ audioFile, callId, callerId, officeId, accountType, startTime, language }: UploadSingleInput) => {
      if (!headers) {
        throw new Error('No active identity selected.')
      }
      const formData = new FormData()
      formData.append('audio_file', audioFile, audioFile.name)
      formData.append('call_id', callId ?? '')
      formData.append('caller_id', callerId)
      formData.append('office_id', officeId)
      formData.append('account_type', accountType)
      formData.append('start_time', startTime)
      formData.append('language', language)
      return apiUpload<UploadBatchResponse>('/api/uploads/single', formData, headers)
    },
  })
}
