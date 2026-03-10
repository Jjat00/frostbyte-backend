from django.core.management.base import BaseCommand
from apps.impostor.models import WordCategory, Word


SEED_DATA = {
    "deportes": {
        "name": "Deportes",
        "icon": "Trophy",
        "words": [
            ("Fútbol", "Tenis"),
            ("Baloncesto", "Voleibol"),
            ("Natación", "Buceo"),
            ("Atletismo", "Gimnasia"),
            ("Boxeo", "Karate"),
            ("Ciclismo", "Patinaje"),
            ("Golf", "Billar"),
            ("Béisbol", "Sóftbol"),
            ("Rugby", "Fútbol americano"),
            ("Esgrima", "Tiro con arco"),
            ("Surf", "Windsurf"),
            ("Esquí", "Snowboard"),
            ("Polo", "Equitación"),
            ("Hockey", "Lacrosse"),
            ("Ping Pong", "Bádminton"),
            ("Lucha libre", "Judo"),
            ("Waterpolo", "Kayak"),
            ("Escalada", "Rappel"),
            ("Patinaje artístico", "Ballet"),
            ("Maratón", "Triatlón"),
        ],
    },
    "artistas": {
        "name": "Artistas/Músicos",
        "icon": "Music",
        "words": [
            ("Bad Bunny", "J Balvin"),
            ("Taylor Swift", "Ariana Grande"),
            ("Drake", "Kendrick Lamar"),
            ("Shakira", "Jennifer López"),
            ("The Weeknd", "Bruno Mars"),
            ("Rihanna", "Beyoncé"),
            ("Ed Sheeran", "Sam Smith"),
            ("Eminem", "50 Cent"),
            ("Daddy Yankee", "Don Omar"),
            ("Karol G", "Natti Natasha"),
            ("Billie Eilish", "Olivia Rodrigo"),
            ("BTS", "Blackpink"),
            ("Coldplay", "Imagine Dragons"),
            ("Dua Lipa", "Lady Gaga"),
            ("Post Malone", "Travis Scott"),
            ("Ozuna", "Anuel AA"),
            ("Maluma", "Sebastián Yatra"),
            ("Queen", "The Beatles"),
            ("Michael Jackson", "Prince"),
            ("Adele", "Amy Winehouse"),
        ],
    },
    "comidas": {
        "name": "Comidas",
        "icon": "UtensilsCrossed",
        "words": [
            ("Pizza", "Hamburguesa"),
            ("Sushi", "Ceviche"),
            ("Tacos", "Burritos"),
            ("Pasta", "Lasaña"),
            ("Paella", "Risotto"),
            ("Hot Dog", "Choripán"),
            ("Empanada", "Arepa"),
            ("Croquetas", "Nuggets"),
            ("Ensalada César", "Ensalada griega"),
            ("Ramen", "Pho"),
            ("Parrillada", "Asado"),
            ("Helado", "Gelato"),
            ("Waffles", "Panqueques"),
            ("Nachos", "Quesadilla"),
            ("Pollo frito", "Pollo asado"),
            ("Sopa", "Consomé"),
            ("Churros", "Donas"),
            ("Pad Thai", "Chow Mein"),
            ("Croissant", "Pan de bono"),
            ("Brownie", "Torta de chocolate"),
        ],
    },
    "peliculas": {
        "name": "Películas",
        "icon": "Film",
        "words": [
            ("Avengers", "Justice League"),
            ("Titanic", "Pearl Harbor"),
            ("Harry Potter", "El Señor de los Anillos"),
            ("Matrix", "Inception"),
            ("Star Wars", "Star Trek"),
            ("Jurassic Park", "King Kong"),
            ("Toy Story", "Monsters Inc"),
            ("El Rey León", "Bambi"),
            ("Frozen", "Enredados"),
            ("Batman", "Superman"),
            ("Spider-Man", "Venom"),
            ("Fast & Furious", "Need for Speed"),
            ("Shrek", "Madagascar"),
            ("Coco", "Encanto"),
            ("Gladiador", "Troya"),
            ("El Padrino", "Scarface"),
            ("Joker", "El Caballero de la Noche"),
            ("Avatar", "Dune"),
            ("Piratas del Caribe", "Moby Dick"),
            ("John Wick", "James Bond"),
        ],
    },
    "paises": {
        "name": "Países",
        "icon": "Globe",
        "words": [
            ("Japón", "China"),
            ("Brasil", "Argentina"),
            ("Francia", "Italia"),
            ("Egipto", "Marruecos"),
            ("Australia", "Nueva Zelanda"),
            ("México", "Colombia"),
            ("Alemania", "Austria"),
            ("India", "Pakistán"),
            ("Canadá", "Estados Unidos"),
            ("Rusia", "Ucrania"),
            ("Corea del Sur", "Corea del Norte"),
            ("España", "Portugal"),
            ("Grecia", "Turquía"),
            ("Tailandia", "Vietnam"),
            ("Cuba", "Puerto Rico"),
            ("Noruega", "Suecia"),
            ("Perú", "Ecuador"),
            ("Sudáfrica", "Nigeria"),
            ("Irlanda", "Escocia"),
            ("Suiza", "Luxemburgo"),
        ],
    },
    "animales": {
        "name": "Animales",
        "icon": "Bug",
        "words": [
            ("León", "Tigre"),
            ("Perro", "Lobo"),
            ("Gato", "Pantera"),
            ("Águila", "Halcón"),
            ("Delfín", "Tiburón"),
            ("Elefante", "Rinoceronte"),
            ("Caballo", "Cebra"),
            ("Mono", "Gorila"),
            ("Pingüino", "Pato"),
            ("Cocodrilo", "Lagarto"),
            ("Serpiente", "Lagartija"),
            ("Oso", "Panda"),
            ("Tortuga", "Caracol"),
            ("Ballena", "Orca"),
            ("Loro", "Tucán"),
            ("Conejo", "Liebre"),
            ("Canguro", "Koala"),
            ("Búho", "Lechuza"),
            ("Pulpo", "Calamar"),
            ("Abeja", "Avispa"),
        ],
    },
    "profesiones": {
        "name": "Profesiones",
        "icon": "Briefcase",
        "words": [
            ("Doctor", "Enfermero"),
            ("Abogado", "Juez"),
            ("Arquitecto", "Ingeniero civil"),
            ("Chef", "Panadero"),
            ("Piloto", "Astronauta"),
            ("Bombero", "Paramédico"),
            ("Policía", "Detective"),
            ("Profesor", "Tutor"),
            ("Periodista", "Escritor"),
            ("Fotógrafo", "Camarógrafo"),
            ("Dentista", "Ortodoncista"),
            ("Psicólogo", "Psiquiatra"),
            ("Veterinario", "Biólogo"),
            ("Electricista", "Plomero"),
            ("Programador", "Diseñador web"),
            ("Contador", "Economista"),
            ("Músico", "DJ"),
            ("Actor", "Director de cine"),
            ("Mecánico", "Ingeniero automotriz"),
            ("Farmacéutico", "Químico"),
        ],
    },
    "fiestas": {
        "name": "Fiestas",
        "icon": "PartyPopper",
        "words": [
            ("Navidad", "Año Nuevo"),
            ("Halloween", "Día de Muertos"),
            ("Carnaval", "Mardi Gras"),
            ("San Valentín", "Día de la Madre"),
            ("Cumpleaños", "Quinceañera"),
            ("Boda", "Despedida de soltero"),
            ("Baby Shower", "Bautizo"),
            ("Graduación", "Primer comunión"),
            ("Semana Santa", "Cuaresma"),
            ("Día de la Independencia", "Día de la Bandera"),
            ("Fiesta de disfraces", "Fiesta temática"),
            ("Nochebuena", "Nochevieja"),
            ("San Patricio", "Oktoberfest"),
            ("Día del Padre", "Día del Abuelo"),
            ("Fiesta de pijamas", "Fiesta de alberca"),
            ("Revelación de género", "Baby shower"),
            ("Festival de música", "Concierto"),
            ("Día del Niño", "Día del Estudiante"),
            ("Posada", "Pastorela"),
            ("Verbena", "Feria"),
        ],
    },
    "marcas": {
        "name": "Marcas",
        "icon": "Tag",
        "words": [
            ("Nike", "Adidas"),
            ("Apple", "Samsung"),
            ("Coca-Cola", "Pepsi"),
            ("McDonald's", "Burger King"),
            ("Netflix", "Disney+"),
            ("Google", "Bing"),
            ("Amazon", "Mercado Libre"),
            ("PlayStation", "Xbox"),
            ("Ferrari", "Lamborghini"),
            ("Gucci", "Louis Vuitton"),
            ("Spotify", "Apple Music"),
            ("Instagram", "TikTok"),
            ("WhatsApp", "Telegram"),
            ("YouTube", "Twitch"),
            ("BMW", "Mercedes-Benz"),
            ("Starbucks", "Dunkin' Donuts"),
            ("Zara", "H&M"),
            ("Uber", "Lyft"),
            ("Toyota", "Honda"),
            ("Rolex", "Omega"),
        ],
    },
    "videojuegos": {
        "name": "Videojuegos",
        "icon": "Gamepad2",
        "words": [
            ("Minecraft", "Terraria"),
            ("Fortnite", "PUBG"),
            ("GTA V", "Saints Row"),
            ("FIFA", "PES"),
            ("Call of Duty", "Battlefield"),
            ("Mario Bros", "Sonic"),
            ("Zelda", "God of War"),
            ("Among Us", "Fall Guys"),
            ("League of Legends", "Dota 2"),
            ("Pokémon", "Digimon"),
            ("Resident Evil", "Silent Hill"),
            ("Overwatch", "Valorant"),
            ("The Sims", "Animal Crossing"),
            ("Roblox", "Rec Room"),
            ("Clash Royale", "Clash of Clans"),
            ("Dark Souls", "Elden Ring"),
            ("Need for Speed", "Gran Turismo"),
            ("Tetris", "Pac-Man"),
            ("Halo", "Destiny"),
            ("Red Dead Redemption", "Assassin's Creed"),
        ],
    },
}


class Command(BaseCommand):
    help = "Seed initial word categories and words for the Impostor game"

    def add_arguments(self, parser):
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Clear existing data before seeding",
        )

    def handle(self, *args, **options):
        if options["clear"]:
            self.stdout.write("Clearing existing impostor data...")
            Word.objects.all().delete()
            WordCategory.objects.all().delete()

        categories_created = 0
        words_created = 0

        for slug, data in SEED_DATA.items():
            category, created = WordCategory.objects.get_or_create(
                slug=slug,
                defaults={
                    "name": data["name"],
                    "icon": data["icon"],
                },
            )
            if created:
                categories_created += 1

            for word_text, trap_text in data["words"]:
                _, w_created = Word.objects.get_or_create(
                    category=category,
                    word=word_text,
                    defaults={
                        "trap_word": trap_text,
                    },
                )
                if w_created:
                    words_created += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Seed complete: {categories_created} categories, {words_created} words created"
            )
        )
