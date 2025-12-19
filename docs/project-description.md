I want to create a web application that allows users to go through a text-based choose your own adventure experience that is completely powered by an LLM.

Like any other choose your own adventure, the story will begin with a little context and then allow the user to make choices as they progress, maybe 2-4 options. Each story can be represented as a tree, where each node represents a block of the story, and edges represent each decision that can be made. Since I will want to make these stories storable and traversable (undo, go forward and backward through choices) the format this data is stored in is important.

One of the key distinguishing factors about my web-application is that I would like to greatly emphasize _narrative consistency_. The world that the user navigates and makes decisions inside of should remain completely consistent, even if the user navigates through various branches. For example, if a user makes choice A that reveals additional information about the world, that info should remain true in any future progressions, even if they undo their choices and go down a different path.

In terms of architecture, I'm thinking of doing this by embedding each node into a vector database and then feeding similar chunks of narrative into the context when generating a new node with an LLM. There may be some additional enhancements that can be done by storing specific things (characters, choices, etc) in particular ways, but for the time being I think this abstraction may be enough.

Another big feature I would like to focus on is making stories "sharable," in the same way that many media platforms (like tiktok) do - users can either begin their own adventures or browse popular (or even recommended) ones. These stories should be "cached" in the sense that any previously explored branches are still available and are continuously reused, while any additional exploration is generated on the fly.

In the long run it may be fun to add in a form of multiplayer - so maybe keep that in mind moving forward.

Here's the ideal user experience for some additional context:

I want to have two main tabs: "Discover" and "Create". In the discover tab, users should be able to swipe through user-generated story/world "templates" or foundations - this is what I mentioned earlier. In the "Create" tab, users should be able to either pick from predefined genres or build their own prompt in free-text format, which will generate a seed "world" for them.
