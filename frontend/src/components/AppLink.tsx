import type { AnchorHTMLAttributes, MouseEvent } from "react";
import { navigate } from "../routes/router";

export function AppLink({ href, children, ...properties }: AnchorHTMLAttributes<HTMLAnchorElement> & { href: string }) {
  const onClick = (event: MouseEvent<HTMLAnchorElement>): void => {
    if (
      event.defaultPrevented ||
      event.button !== 0 ||
      event.metaKey ||
      event.ctrlKey ||
      event.shiftKey ||
      event.altKey
    ) return;
    event.preventDefault();
    navigate(href);
  };
  return <a href={href} {...properties} onClick={onClick}>{children}</a>;
}
