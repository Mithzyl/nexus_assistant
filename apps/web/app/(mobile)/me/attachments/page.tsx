import { ImageAttachmentScreen } from "@/components/mobile/image-attachment-screen";

/** Keep the selected matter in the link so the shared login page can return to it. */
export default function ImageAttachmentsPage({ searchParams }: { searchParams: { matter?: string | string[] } }) {
  const matterId = typeof searchParams.matter === "string" && searchParams.matter ? searchParams.matter : undefined;
  return <ImageAttachmentScreen key={matterId ?? "all"} matterId={matterId} />;
}
