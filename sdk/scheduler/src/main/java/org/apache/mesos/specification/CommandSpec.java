package org.apache.mesos.specification;

import com.fasterxml.jackson.annotation.JsonProperty;
import com.fasterxml.jackson.databind.annotation.JsonDeserialize;

import java.net.URI;
import java.util.Collection;
import java.util.Map;
import java.util.Optional;

@JsonDeserialize(as = DefaultCommandSpec.class)
public interface CommandSpec {
    @JsonProperty("value")
    String getValue();

    @JsonProperty("environment")
    Map<String, String> getEnvironment();

    @JsonProperty("user")
    Optional<String> getUser();

    @JsonProperty("uris")
    Collection<URI> getUris();
}
