import { createBrowserRouter, redirect } from "react-router";
import { Layout } from "./components/Layout";
import { Loading } from "./components/Loading";
import { RouteError } from "./components/RouteError";

export const router = createBrowserRouter([
  {
    path: "/",
    Component: Layout,
    HydrateFallback: Loading,
    ErrorBoundary: RouteError,
    children: [
      {
        index: true,
        lazy: async () => ({ Component: (await import("./pages/Overview")).Overview }),
      },
      {
        path: "map",
        lazy: async () => ({ Component: (await import("./pages/CommunityMap")).CommunityMap }),
      },
      {
        path: "channel/:id",
        lazy: async () => ({ Component: (await import("./pages/ChannelDetail")).ChannelDetail }),
      },
      {
        path: "results",
        lazy: async () => ({ Component: (await import("./pages/Results")).Results }),
      },
      {
        path: "methods",
        lazy: async () => ({ Component: (await import("./pages/Methods")).Methods }),
      },
      // The pages' names before the redesign, kept so old links still land.
      { path: "stats", Component: Loading, loader: () => redirect("/results") },
      { path: "about", Component: Loading, loader: () => redirect("/methods") },
      {
        path: "*",
        lazy: async () => ({ Component: (await import("./pages/NotFound")).NotFound }),
      },
    ],
  },
]);
