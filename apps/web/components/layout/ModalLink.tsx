"use client";

import Link from "next/link";
import type { ComponentProps, MouseEvent } from "react";
import { isModalPath } from "@/lib/modal-workspace/module-registry";
import { useModalWorkspace } from "./ModalWorkspaceProvider";

type ModalLinkProps = Omit<ComponentProps<typeof Link>, "href"> & {
  href: string;
};

export function ModalLink({ href, onClick, ...props }: ModalLinkProps) {
  const { openModule, closeModule } = useModalWorkspace();
  const isHome = href === "/" || href === "";
  const isInternal = href.startsWith("/");
  const useModal = isInternal && (isHome || isModalPath(href.split("?")[0] ?? href));

  if (!useModal) {
    return <Link href={href} onClick={onClick} {...props} />;
  }

  const handleClick = (event: MouseEvent<HTMLAnchorElement>) => {
    onClick?.(event);
    if (event.defaultPrevented) return;
    event.preventDefault();
    if (isHome) {
      closeModule();
      return;
    }
    openModule(href);
  };

  return (
    <Link href={href} onClick={handleClick} scroll={false} {...props} />
  );
}
