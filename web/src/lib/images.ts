// 把用户粘贴或选择的图片转成接口要的 base64；太大的先缩小
import type { ImageInput } from '../api'

const MAX_SIDE = 1600
const TYPES = ['image/png', 'image/jpeg', 'image/gif', 'image/webp']

export interface PendingImage extends ImageInput {
  url: string
  name: string
}

function readAsDataURL(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result))
    reader.onerror = () => reject(reader.error)
    reader.readAsDataURL(blob)
  })
}

async function shrink(file: File): Promise<Blob> {
  if (file.type === 'image/gif' || file.size < 600_000) return file
  const bitmap = await createImageBitmap(file)
  const scale = Math.min(1, MAX_SIDE / Math.max(bitmap.width, bitmap.height))
  if (scale === 1 && file.size < 3_000_000) return file
  const canvas = document.createElement('canvas')
  canvas.width = Math.round(bitmap.width * scale)
  canvas.height = Math.round(bitmap.height * scale)
  canvas.getContext('2d')!.drawImage(bitmap, 0, 0, canvas.width, canvas.height)
  return new Promise((resolve) => canvas.toBlob((b) => resolve(b || file), 'image/jpeg', 0.85))
}

export async function toImage(file: File): Promise<PendingImage> {
  if (!TYPES.includes(file.type)) throw new Error('只支持 PNG、JPEG、GIF、WebP 图片')
  const blob = await shrink(file)
  const url = await readAsDataURL(blob)
  const [head, data] = url.split(',', 2)
  const media_type = head.slice(5, head.indexOf(';'))
  if (data.length > 7_000_000) throw new Error('图片太大了，单张不能超过 5 MB')
  return { media_type, data, url, name: file.name || '截图' }
}
