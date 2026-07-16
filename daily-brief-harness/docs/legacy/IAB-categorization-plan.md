right now, the TOPICS shown are the 11 public categories available on gnews searching -
AI & Machine Learning
Semiconductors & Hardware
Startups & VC
Big Tech
Cybersecurity
India Tech & Startups
Business & Economy
Science & Space
World News
Politics
Sports -
what our backend follows (not present here in the code base, but it is must that we integrate this) is something different. WE classify content based on the following -
Books and Literature and Fine Arts,
Business and Finance,
Careers,
Education,
Events,
Attractions & Pop Culture,
Family and Relationships,
Food & Drink,
Healthy Living & medical health,
Hobbies & Interests,
Home & Garden, Movies,
Music and Audio,
News and Politics,
Real Estate,
Religion & Spirituality,
Science,
Shopping,
Sports,
Style & Fashion,
Technology & Computing,
Television,
Travel -
These are much broader than the categories that we are searching with. This means that we still haven't integrated a proper way to map from our categorization to a mechanism for fetching from gnews

the fix - use both category-wise/feed querying as well as searching in the gnews api -
IAB_TO_GNEWS = {
"Books and Literature and Fine Arts": {
"type": "search",
"query": '"books" OR "literature" OR "author" OR "publishing" OR "art" OR "museum"'
},

    "Business and Finance": {
        "type": "feed",
        "topics": [
            "Business & Economy",
            "Startups & Venture Capital"
        ]
    },

    "Careers": {
        "type": "search",
        "query": '"jobs" OR "employment" OR "career" OR "hiring" OR "workplace"'
    },

    "Education": {
        "type": "search",
        "query": '"education" OR "schools" OR "universities" OR "students"'
    },

    "Events, Attractions & Pop Culture": {
        "type": "search",
        "query": '"pop culture" OR celebrity OR festival OR event OR entertainment'
    },

    "Family and Relationships": {
        "type": "search",
        "query": '"family" OR parenting OR relationships'
    },

    "Food & Drink": {
        "type": "search",
        "query": 'food OR restaurant OR recipe OR beverage'
    },

    "Healthy Living & medical health": {
        "type": "search",
        "query": '"public health" OR medicine OR healthcare OR wellness OR fitness'
    },

    "Hobbies & Interests": {
        "type": "search",
        "query": 'hobbies OR DIY OR photography OR gaming OR crafts'
    },

    "Home & Garden": {
        "type": "search",
        "query": '"home improvement" OR gardening OR interior design'
    },

    "Movies": {
        "type": "search",
        "query": 'movies OR film OR cinema'
    },

    "Music and Audio": {
        "type": "search",
        "query": 'music OR album OR artist OR concert'
    },

    "News and Politics": {
        "type": "feed",
        "topics": [
            "World News",
            "Politics"
        ]
    },

    "Real Estate": {
        "type": "search",
        "query": '"real estate" OR housing OR property'
    },

    "Religion & Spirituality": {
        "type": "search",
        "query": 'religion OR spirituality OR faith'
    },

    "Science": {
        "type": "feed",
        "topics": [
            "Science & Space"
        ]
    },

    "Shopping": {
        "type": "search",
        "query": 'shopping OR retail OR ecommerce OR consumer'
    },

    "Sports": {
        "type": "feed",
        "topics": [
            "Sports"
        ]
    },

    "Style & Fashion": {
        "type": "search",
        "query": 'fashion OR style OR luxury OR runway'
    },

    "Technology & Computing": {
        "type": "feed",
        "topics": [
            "AI & Machine Learning",
            "Semiconductors & Hardware",
            "Tech Giants (Big Tech)"
        ]
    },

    "Television": {
        "type": "search",
        "query": 'television OR TV OR streaming OR Netflix'
    },

    "Travel": {
        "type": "search",
        "query": 'travel OR tourism OR airlines OR hotels'
    }

}
wherever it's a "feed" type - we search the usual way we do, wherever it's "search" we use the search by keyword method.
