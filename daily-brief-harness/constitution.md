The purpose of this feature is to integrate it with an app - Curia - which takes a user's saved/bookmarked articles and finds patterns to create a concise and cohesive audio show (a kind of podcast). To trigger this event, the user has to share a link with curia or upload a link to curia.\
With daily brief, we're extending the scope of the app - notify users daily with the latest news which is primarily related to the topics of their saved articles and then secondarily to global or local news depending upon the user's location as well.\
Intro-added-last logic - \
Initially, generate a transcript json array `[{"host": xyz, "text":xyz}, ---]` that contains everything (-to-be-decided) except for the intro. When the user decides to actually play it, the intro should be concatenated at the front of the transcript in a personalized manner - "hello `<user>`, we had our last chat about `<days-since-last-brief>` ago i believe, glad you're here again. If you plan on going out today, the weather seems a little `<location-weather forecast>`, might want to carry an umbrella/wear light clothes" - something along those lines.\
What the brief itself (everything except for the intro) shuold be limited to around 650 words and the content should contain - \

- Latest news on the "user's most-recent saved links", sorted by how groundbreaking it is, limited to 3-4.
- Latest news relevant to user's location, if not specified, default location is India, location could be more specific like mumbai, toronto, texas, tokyo too.

`(The brief content must be up-to-date, primary users would save links every day, and listen to the brief everyday. Further, the brief should not repeat itself, so for one user, even a huge news like "Iran signs the agreement with USA" will only be generated only once. If there's any new progress on any further day, like "Trump says Iran has broken international law by doing something against their agreement" then it should be surfaced as a candidate for new, but if the news fetching detects an article about "Iran signing the agreement than Donald J. Trump proposed", then it should be rejected, since it is the same content)`

Dashboard Purpose -\
The Dashboard is where we should be able to test the working of our feature. Currently the existing dashboard is good enough for us to build on top of or parallel to it.\
The dashboard should have configurable parameters for testing purposes - `(for example, in the above paragraph about how the brief must be up-to-date, if i change the username in the dashboard and then fetch the news for the exact same inputs (interests, saved articles etc.) then it should surface the same article again (provided it's on the same day))`\
