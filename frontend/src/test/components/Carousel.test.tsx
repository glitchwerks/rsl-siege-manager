import { screen, fireEvent, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect } from "vitest";
import { renderWithProviders } from "../utils";
import Carousel, { type CarouselSlide } from "../../components/Carousel";

const SLIDES: CarouselSlide[] = [
  { placeholder: "Slide A", title: "Title A", description: "Desc A" },
  { placeholder: "Slide B", title: "Title B", description: "Desc B" },
  { placeholder: "Slide C", title: "Title C", description: "Desc C" },
];

function renderCarousel(slides = SLIDES) {
  return renderWithProviders(<Carousel slides={slides} />);
}

describe("Carousel", () => {
  it("renders the first slide by default", () => {
    renderCarousel();
    const track = screen.getByTestId("carousel-track");
    expect(track).toHaveStyle({ transform: "translateX(-0%)" });
  });

  it("renders all slide titles", () => {
    renderCarousel();
    SLIDES.forEach((s) => {
      expect(screen.getByText(s.title)).toBeInTheDocument();
    });
  });

  it("renders a lazy-loaded image using the slide title as alt text", () => {
    const imageSlide: CarouselSlide = {
      placeholder: "Image fallback",
      title: "Assignment board",
      description: "Assignment board screenshot",
      image: "/landing/carousel-assignment-board.png",
    };

    renderCarousel([imageSlide]);

    const image = screen.getByRole("img", { name: imageSlide.title });
    expect(image).toHaveAttribute("src", imageSlide.image);
    expect(image).toHaveAttribute("loading", "lazy");
  });

  it("renders the placeholder instead of an image when no image is provided", () => {
    renderCarousel([SLIDES[0]]);

    const slide = screen.getByTestId("carousel-slide-0");
    const placeholder = within(slide).getByText(SLIDES[0].placeholder);
    expect(within(slide).queryByRole("img")).not.toBeInTheDocument();
    expect(placeholder).toBeInTheDocument();
    expect(placeholder.parentElement?.tagName).toBe("DIV");
  });

  it("renders one dot per slide", () => {
    renderCarousel();
    SLIDES.forEach((_, i) => {
      expect(screen.getByTestId(`carousel-dot-${i}`)).toBeInTheDocument();
    });
  });

  it("first dot is highlighted (violet) on initial render", () => {
    renderCarousel();
    const dot0 = screen.getByTestId("carousel-dot-0");
    expect(dot0).toHaveStyle({ backgroundColor: "#7c3aed" });
  });

  it("advances to slide 2 when Next is clicked", async () => {
    const user = userEvent.setup();
    renderCarousel();
    await user.click(screen.getByTestId("carousel-next"));
    const track = screen.getByTestId("carousel-track");
    expect(track).toHaveStyle({ transform: "translateX(-100%)" });
  });

  it("highlights the correct dot after advancing", async () => {
    const user = userEvent.setup();
    renderCarousel();
    await user.click(screen.getByTestId("carousel-next"));
    expect(screen.getByTestId("carousel-dot-1")).toHaveStyle({
      backgroundColor: "#7c3aed",
    });
    expect(screen.getByTestId("carousel-dot-0")).toHaveStyle({
      backgroundColor: "#cbd5e1",
    });
  });

  it("goes back when Prev is clicked", async () => {
    const user = userEvent.setup();
    renderCarousel();
    // Go to slide 2 first
    await user.click(screen.getByTestId("carousel-next"));
    // Then go back to slide 1
    await user.click(screen.getByTestId("carousel-prev"));
    expect(screen.getByTestId("carousel-track")).toHaveStyle({
      transform: "translateX(-0%)",
    });
  });

  it("wraps around to last slide when Prev is clicked on first slide", async () => {
    const user = userEvent.setup();
    renderCarousel();
    await user.click(screen.getByTestId("carousel-prev"));
    // Should land on last slide (index 2 of 3)
    expect(screen.getByTestId("carousel-track")).toHaveStyle({
      transform: "translateX(-200%)",
    });
  });

  it("wraps around to first slide when Next is clicked on last slide", async () => {
    const user = userEvent.setup();
    renderCarousel();
    // Advance to last slide
    await user.click(screen.getByTestId("carousel-next"));
    await user.click(screen.getByTestId("carousel-next"));
    // Now on slide 3 (index 2). One more Next should wrap to 0.
    await user.click(screen.getByTestId("carousel-next"));
    expect(screen.getByTestId("carousel-track")).toHaveStyle({
      transform: "translateX(-0%)",
    });
  });

  it("jumps to a specific slide when its dot is clicked", async () => {
    const user = userEvent.setup();
    renderCarousel();
    await user.click(screen.getByTestId("carousel-dot-2"));
    expect(screen.getByTestId("carousel-track")).toHaveStyle({
      transform: "translateX(-200%)",
    });
  });

  it("renders prev and next arrow buttons", () => {
    renderCarousel();
    expect(screen.getByTestId("carousel-prev")).toBeInTheDocument();
    expect(screen.getByTestId("carousel-next")).toBeInTheDocument();
  });

  it("ArrowRight advances and ArrowLeft retreats when the viewport is focused", () => {
    renderCarousel();
    const viewport = screen.getByTestId("carousel-viewport");
    viewport.focus();

    // ArrowRight → slide 1
    fireEvent.keyDown(viewport, { key: "ArrowRight" });
    expect(screen.getByTestId("carousel-track")).toHaveStyle({
      transform: "translateX(-100%)",
    });

    // ArrowLeft → back to slide 0
    fireEvent.keyDown(viewport, { key: "ArrowLeft" });
    expect(screen.getByTestId("carousel-track")).toHaveStyle({
      transform: "translateX(-0%)",
    });
  });
});
