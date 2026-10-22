import { useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "./ui/dialog";
import type { GuideImage } from "../guides/types";

export default function GuideScreenshot({ image }: { image: GuideImage }) {
  const [open, setOpen] = useState(false);
  return (
    <figure className="my-6 overflow-hidden rounded-lg border border-slate-200 bg-slate-50">
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="group block w-full cursor-zoom-in text-left focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-violet-600"
        aria-label={`Enlarge screenshot: ${image.title}`}
      >
        <div
          className={image.previewCrop ? "overflow-hidden" : undefined}
          style={
            image.previewCrop
              ? { aspectRatio: `${image.width} / ${image.previewCrop.height}` }
              : undefined
          }
        >
          <img
            src={image.src}
            alt={image.alt}
            width={image.width}
            height={image.height}
            loading="lazy"
            className="h-auto w-full"
            style={
              image.previewCrop
                ? {
                    marginTop: `-${(image.previewCrop.top / image.width) * 100}%`,
                  }
                : undefined
            }
          />
        </div>
        <span className="block border-t border-slate-200 px-4 py-2 text-xs font-semibold text-violet-700 group-hover:underline">
          Click or tap to enlarge
        </span>
      </button>
      <figcaption className="px-4 pb-4 text-sm leading-relaxed text-slate-600">
        <strong className="text-slate-900">{image.title}.</strong>{" "}
        {image.caption}{" "}
        <span className="text-slate-500">
          Example uses fictional demo members.
        </span>
      </figcaption>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-h-[95dvh] w-[96vw] max-w-6xl overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{image.title}</DialogTitle>
            <DialogDescription>
              {image.caption} Example uses fictional demo members.
            </DialogDescription>
          </DialogHeader>
          <a
            href={image.src}
            target="_blank"
            rel="noopener noreferrer"
            className="mb-2 text-sm text-violet-700 underline"
          >
            Open full-size image in a new tab
          </a>
          <img
            src={image.src}
            alt={image.alt}
            width={image.width}
            height={image.height}
            className="h-auto w-full rounded border border-slate-200"
          />
        </DialogContent>
      </Dialog>
    </figure>
  );
}
