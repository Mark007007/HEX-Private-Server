pub mod server {
    pub struct ServerConfig {
        pub bind: String,
    }

    impl Default for ServerConfig {
        fn default() -> Self {
            Self {
                bind: "127.0.0.1:9933".to_string(),
            }
        }
    }
}
