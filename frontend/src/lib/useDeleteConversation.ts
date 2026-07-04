import { useMutation, useQueryClient } from '@tanstack/react-query'
import { deleteConversation } from './askApi'

export function useDeleteConversation() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => deleteConversation(id),
    onSuccess: () => { void qc.invalidateQueries({ queryKey: ['conversations'] }) },
  })
}
